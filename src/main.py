"""Run the newsletter end to end.

    python src/main.py                 # the real thing: fetch, write, render, send
    python src/main.py --dry-run       # everything except the send
    python src/main.py --no-send       # alias for --dry-run
    python src/main.py --force         # ignore the Sun-Thu schedule
    python src/main.py --date 2026-07-30

Failure policy is abort-and-alert: any stage that can't produce trustworthy
output stops the run, emails the operator a traceback, and exits non-zero. A
half-correct market briefing is worse than no market briefing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

import history  # noqa: E402
import mailer  # noqa: E402
import market  # noqa: E402
import render  # noqa: E402
import research  # noqa: E402

log = logging.getLogger("newsletter")

# Jordan is UTC+3 year-round — it abolished DST in 2022, so a fixed offset is
# correct here and avoids a tzdata dependency on Windows.
AMMAN = timezone(timedelta(hours=3))


def long_date(d: date) -> str:
    """`July 30, 2026` — no zero padding, matching the original."""
    return f"{d:%B} {d.day}, {d.year}"


def headline_for(content: dict) -> str:
    """First sentence of the forex lede, for the email body."""
    paras = content.get("forex") or []
    if not paras:
        return ""
    first = paras[0].strip()
    cut = first.find(". ")
    return first if cut == -1 else first[: cut + 1]


def run(config: dict, today: date, send: bool) -> int:
    date_long = long_date(today)
    iso = today.isoformat()
    out_dir = ROOT / "out"
    pdf_path = out_dir / f"News in 60 Seconds - {iso}.pdf"

    stage = "startup"
    try:
        state = history.load(ROOT, config)
        edition = history.edition_for(
            state, iso, config["masthead"].get("edition_start", 1)
        )
        log.info("edition No. %d", edition)

        stage = "market data"
        log.info("[1/4] fetching market data")
        prices = market.fetch(config)

        stage = "research"
        log.info("[2/4] researching and writing")
        content = research.generate(config, prices, today, date_long, state)

        stage = "render"
        log.info("[3/4] rendering the page")
        render.render_pdf(config, content, prices, date_long, pdf_path, edition)

        # Same rule as the ledger below: a dry run is a rehearsal, so it must
        # not touch the archive either. CI commits archive/ back to the repo,
        # which means an unguarded rehearsal replaces the stored copy of an
        # issue that already went out — different copy, same filename, and the
        # real one is gone. The build still lands in out/ for inspection.
        if send and config.get("archive", {}).get("enabled"):
            archive_dir = ROOT / config["archive"]["dir"]
            archive_dir.mkdir(parents=True, exist_ok=True)
            (archive_dir / f"{iso}.pdf").write_bytes(pdf_path.read_bytes())
            log.info("  archived to %s/%s.pdf", config["archive"]["dir"], iso)

        stage = "send"
        if not send:
            log.info("[4/4] dry run — not sending or archiving. PDF at %s", pdf_path)
            return 0

        log.info("[4/4] sending")
        recipients = mailer.resolve_recipients(ROOT)
        mailer.send_newsletter(
            config, pdf_path, date_long, iso, headline_for(content), recipients
        )

        # Only a delivered issue goes in the ledger. A dry run is a rehearsal:
        # it should not burn an edition number or retire a quote.
        history.record(ROOT, config, state, iso, edition, content.get("quote", {}))

        log.info("done — %s delivered", iso)
        return 0

    except (market.MarketDataError, research.ResearchError,
            render.RenderError, mailer.MailError) as exc:
        log.error("FAILED at %s: %s", stage, exc)
        mailer.send_failure_alert(config, stage, str(exc), traceback.format_exc(), iso)
        return 1
    except Exception as exc:  # noqa: BLE001 - anything unexpected still gets reported
        log.exception("UNEXPECTED failure at %s", stage)
        mailer.send_failure_alert(config, stage, repr(exc), traceback.format_exc(), iso)
        return 1


def load_dotenv(path: Path) -> None:
    """Read KEY=value lines from `.env` into the environment, if it exists.

    Deliberately tiny and dependency-free: it only fills in variables that
    aren't already set, so a real environment — GitHub Actions handing us
    repository secrets — always wins over a file left on a laptop.
    """
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    ap = argparse.ArgumentParser(description="Build and send News in 60 Seconds.")
    ap.add_argument("--dry-run", "--no-send", dest="dry_run", action="store_true",
                    help="build the PDF but don't email it")
    ap.add_argument("--force", action="store_true",
                    help="run even on a non-publishing weekday")
    ap.add_argument("--date", help="override the issue date (YYYY-MM-DD)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # yfinance chatters on every download; we do our own logging.
    logging.getLogger("yfinance").setLevel(logging.ERROR)

    config = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))

    today = (
        date.fromisoformat(args.date) if args.date
        else datetime.now(AMMAN).date()
    )

    allowed = config["schedule"]["send_weekdays"]
    if today.weekday() not in allowed and not args.force:
        log.info("%s is a %s — not a publishing day. Use --force to override.",
                 today.isoformat(), today.strftime("%A"))
        return 0

    # A dry run should never need mail credentials; a real run should fail fast
    # rather than after spending money on research.
    send = not args.dry_run
    if send and not os.environ.get("GMAIL_APP_PASSWORD"):
        log.error("GMAIL_APP_PASSWORD is not set. Use --dry-run to build without sending.")
        return 1

    log.info("News in 60 Seconds — %s (%s)", long_date(today), today.strftime("%A"))
    return run(config, today, send)


if __name__ == "__main__":
    raise SystemExit(main())
