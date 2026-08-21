"""Regression-check the pill arithmetic against the real July 27, 2025 issue.

The original newsletter is the closest thing to a spec that exists, so this pulls
the same window out of yfinance and checks the numbers come back.

Two different standards, on purpose:

  COMMODITIES are asserted. They come from true futures settlements (GC/SI/BZ/CL)
  and the original reproduces to the digit — Brent 68.44 (1.07), WTI 65.16 (1.32).
  If this ever breaks, the arithmetic or the feed has genuinely regressed.

  CURRENCIES are reported, not asserted. The original's FX figures came from
  whatever terminal the 2025 pipeline read, and that source is gone. Neither
  yfinance option reproduces them exactly: the =X tickers are single daily
  snapshots (open == close) taken at a loose hour, and the CME futures carry
  basis. The table below exists so the gap stays visible instead of being
  quietly assumed away.

    python tests/test_against_sample.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

SESSION, PRIOR = date(2025, 7, 25), date(2025, 7, 24)

# Straight off the PDF: label -> (printed price, printed change, colour)
COMMODITIES = {
    "XAU":   (3337.18, 0.95, "down", "GC=F"),
    "XAG":   (38.14,   2.35, "down", "SI=F"),
    "BRENT": (68.44,   1.07, "down", "BZ=F"),
    "WTI":   (65.16,   1.32, "down", "CL=F"),
}
CURRENCIES = {
    "EUR": (1.1744, 0,  "flat", "EURUSD=X", ("6E=F", False)),
    "GBP": (1.3439, 1,  "up",   "GBPUSD=X", ("6B=F", False)),
    "CAD": (1.3699, 58, "up",   "USDCAD=X", ("6C=F", True)),
    "CHF": (0.7955, 1,  "up",   "USDCHF=X", ("6S=F", True)),
    "AUD": (0.6569, 27, "down", "AUDUSD=X", ("6A=F", False)),
    "JPY": (147.66, 9,  "up",   "USDJPY=X", ("6J=F", True)),
}

PRICE_TOL = 0.004    # 0.4% on commodity price (spot vs front-month futures)
CHANGE_TOL = 0.25    # percentage points on commodity change


def closes(symbol: str) -> dict[date, float]:
    import yfinance as yf

    frame = yf.download(symbol, start="2025-07-21", end="2025-07-29", interval="1d",
                        progress=False, auto_adjust=False, threads=False)
    if frame is None or frame.empty or "Close" not in frame:
        return {}
    series = frame["Close"]
    if hasattr(series, "columns"):
        series = series.iloc[:, 0]
    return {i.date(): float(v) for i, v in series.dropna().items()}


def main() -> int:
    failures = 0

    print("\nCOMMODITIES — asserted against the original issue")
    print(f"  {'':<7} {'sym':<6} {'PDF':>9} {'feed':>9} {'off%':>6}   "
          f"{'PDF chg':>8} {'feed':>7} {'dir':>6}  verdict")
    print("  " + "-" * 76)

    for label, (want_px, want_chg, want_dir, sym) in COMMODITIES.items():
        by_date = closes(sym)
        if SESSION not in by_date or PRIOR not in by_date:
            print(f"  {label:<7} {sym:<6}  MISSING SESSION DATA")
            failures += 1
            continue

        px, prev = by_date[SESSION], by_date[PRIOR]
        raw = (px - prev) / prev * 100
        chg, got_dir = abs(round(raw, 2)), ("up" if raw > 0 else "down" if raw < 0 else "flat")

        off = abs(px - want_px) / want_px
        bad = [n for n, ok in (("price", off <= PRICE_TOL),
                               ("change", abs(chg - want_chg) <= CHANGE_TOL),
                               ("dir", got_dir == want_dir)) if not ok]
        failures += bool(bad)
        print(f"  {label:<7} {sym:<6} {want_px:>9.2f} {px:>9.2f} {off*100:>5.2f}%   "
              f"{want_chg:>8} {chg:>7} {got_dir:>6}  "
              f"{'ok' if not bad else 'MISMATCH: ' + ','.join(bad)}")

    print("\nCURRENCIES — reported only; the original's source no longer exists")
    print(f"  {'':<5} {'PDF':>9} {'=X spot':>9} {'off%':>6} {'futures':>9} {'off%':>6}   closer")
    print("  " + "-" * 68)

    for label, (want_px, _chg, _dir, spot_sym, (fut_sym, invert)) in CURRENCIES.items():
        spot = closes(spot_sym).get(SESSION)
        fut_raw = closes(fut_sym).get(SESSION)
        fut = (1.0 / fut_raw) if (fut_raw and invert) else fut_raw

        s_off = abs(spot - want_px) / want_px * 100 if spot else float("inf")
        f_off = abs(fut - want_px) / want_px * 100 if fut else float("inf")
        winner = "futures" if f_off < s_off else "=X spot"

        print(f"  {label:<5} {want_px:>9.4f} "
              f"{(f'{spot:.4f}' if spot else 'n/a'):>9} {s_off:>5.2f}% "
              f"{(f'{fut:.4f}' if fut else 'n/a'):>9} {f_off:>5.2f}%   {winner}")

    print()
    if failures:
        print(f"FAIL — {failures} commodity check(s) did not match the original issue.")
        return 1
    print("PASS — all 4 commodities reproduce the July 27, 2025 issue.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main())
