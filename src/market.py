"""Market data for the pill tables.

No language model touches any number in this file. Prices come from yfinance,
the deltas are arithmetic, and the up/down/flat colour is a comparison. If the
feed can't produce a clean pair of closes for every instrument, this module
raises and the whole run aborts rather than shipping a plausible-looking guess.

Change units, reverse-engineered from the original newsletter:

    currencies  relative basis points   (last - prev) / prev * 10_000  -> int
    commodities relative percent        (last - prev) / prev * 100     -> 2dp

Both are printed as absolute values; the sign only decides the colour and arrow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

UP, DOWN, FLAT = "up", "down", "flat"


class MarketDataError(RuntimeError):
    """Raised when the feed can't be trusted. Always fatal — never send on this."""


@dataclass(frozen=True)
class Quote:
    """One row of a pill table."""

    label: str          # "EUR", "BRENT"
    symbol: str         # yfinance ticker actually used
    price: float
    previous: float
    change_value: float  # bps for FX, percent for commodities; already absolute
    direction: str       # UP | DOWN | FLAT
    decimals: int
    unit: str            # "bps" | "pct"
    as_of: date          # the session the price closed on

    @property
    def price_text(self) -> str:
        # No thousands separator — the original prints `XAU: 3337.18`.
        return f"{self.price:.{self.decimals}f}"

    @property
    def change_text(self) -> str:
        return f"{self.change_value:.0f}" if self.unit == "bps" else f"{self.change_value:.2f}"

    @property
    def display(self) -> str:
        """Exactly how it reads on the page: `CAD: 1.3699 (58)`."""
        return f"{self.label}: {self.price_text} ({self.change_text})"


def _closes(symbol: str, lookback_days: int) -> list[tuple[date, float]]:
    """Return [(session_date, close)] ascending, or [] if the feed has nothing."""
    import yfinance as yf

    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)

    try:
        frame = yf.download(
            symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001 - yfinance raises a zoo of exception types
        log.warning("yfinance raised for %s: %s", symbol, exc)
        return []

    if frame is None or frame.empty or "Close" not in frame:
        return []

    series = frame["Close"]
    # A single-ticker download can still come back with a one-column frame.
    if hasattr(series, "columns"):
        series = series.iloc[:, 0]
    series = series.dropna()

    out: list[tuple[date, float]] = []
    for idx, value in series.items():
        stamp = idx.date() if hasattr(idx, "date") else idx
        price = float(value)
        # Guard against the zero/negative prints yfinance occasionally emits.
        if price > 0:
            out.append((stamp, price))
    return out


def _fetch_with_fallbacks(
    sources: Sequence[dict[str, Any]], lookback_days: int
) -> tuple[str, list[tuple[date, float]]]:
    """Walk the source list until one yields two usable closes.

    Both closes come from the same series, so the printed price and the change
    always reconcile with each other even when a fallback kicks in.
    """
    for position, source in enumerate(sources):
        symbol = source["symbol"]
        rows = _closes(symbol, lookback_days)
        if len(rows) >= 2:
            if position:
                log.info("  falling back to %s", symbol)
            if source.get("invert"):
                rows = [(d, 1.0 / p) for d, p in rows]
            return symbol, rows
        log.warning("  no usable data from %s (%d rows)", symbol, len(rows))

    listed = ", ".join(s["symbol"] for s in sources)
    raise MarketDataError(
        f"none of [{listed}] returned two usable daily closes in the last "
        f"{lookback_days} days"
    )


def _build(
    spec: dict[str, Any], unit: str, lookback_days: int, max_staleness_days: int
) -> Quote:
    used, rows = _fetch_with_fallbacks(spec["sources"], lookback_days)

    (_, previous), (as_of, price) = rows[-2], rows[-1]

    staleness = (datetime.utcnow().date() - as_of).days
    if staleness > max_staleness_days:
        raise MarketDataError(
            f"{spec['label']} ({used}): newest close is {as_of}, {staleness} days old "
            f"(limit {max_staleness_days})"
        )
    if previous <= 0:
        raise MarketDataError(f"{spec['label']} ({used}): non-positive previous close")

    scale = 10_000 if unit == "bps" else 100
    raw = (price - previous) / previous * scale
    rounded = round(raw) if unit == "bps" else round(raw, 2)

    # The colour follows what's *printed*, not the underlying float. A move too
    # small to show up as a non-zero number reads as flat, which is how the
    # original produced its grey EUR row.
    if rounded > 0:
        direction = UP
    elif rounded < 0:
        direction = DOWN
    else:
        direction = FLAT

    return Quote(
        label=spec["label"],
        symbol=used,
        price=price,
        previous=previous,
        change_value=abs(rounded),
        direction=direction,
        decimals=spec.get("decimals", 4),
        unit=unit,
        as_of=as_of,
    )


def fetch(config: dict[str, Any]) -> dict[str, Any]:
    """Fetch every instrument in the config. Raises MarketDataError on any failure."""
    market = config.get("market", {})
    lookback = int(market.get("lookback_days", 10))
    staleness = int(market.get("max_staleness_days", 5))

    def build_all(specs: Iterable[dict[str, Any]], unit: str) -> list[Quote]:
        out = []
        for spec in specs:
            quote = _build(spec, unit, lookback, staleness)
            log.info(
                "  %-6s %-10s %12s  (%s %s) %s",
                quote.label,
                quote.symbol,
                quote.price_text,
                quote.change_text,
                quote.unit,
                quote.direction,
            )
            out.append(quote)
        return out

    log.info("fetching currencies")
    currencies = build_all(config["currencies"], "bps")
    log.info("fetching commodities")
    commodities = build_all(config["commodities"], "pct")

    as_of = max(q.as_of for q in currencies + commodities)
    return {"currencies": currencies, "commodities": commodities, "as_of": as_of}


def summarise(data: dict[str, Any]) -> str:
    """A compact text block handed to Claude so its prose matches the table."""
    lines = ["CURRENCIES (value, change in basis points, direction):"]
    for q in data["currencies"]:
        lines.append(f"  {q.label}: {q.price_text}  {q.change_text} bps  {q.direction}")
    lines.append("COMMODITIES (value, change in percent, direction):")
    for q in data["commodities"]:
        lines.append(f"  {q.label}: {q.price_text}  {q.change_text}%  {q.direction}")
    lines.append(f"Latest session: {data['as_of'].isoformat()}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys
    from pathlib import Path

    import yaml

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text("utf-8"))
    try:
        print(summarise(fetch(cfg)))
    except MarketDataError as exc:
        sys.exit(f"FAILED: {exc}")
