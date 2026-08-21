"""Render the layout from the fixture — no API calls, no email.

    python scripts/preview.py            # PDF + PNG into out/
    python scripts/preview.py --png      # PNG only, for quick visual iteration

This is the loop for working on templates/newsletter.html.j2.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import yaml  # noqa: E402

import render  # noqa: E402
from fixture_original_issue import CONTENT, DATE_LONG, MARKET  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", action="store_true", help="PNG only")
    ap.add_argument("--scale", type=int, default=2, help="PNG device scale factor")
    ap.add_argument("--edition", type=int, default=1,
                    help="edition number to print in the masthead")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    out = ROOT / "out"

    try:
        if not args.png:
            fit = render.render_pdf(config, CONTENT, MARKET, DATE_LONG,
                                    out / "preview.pdf", args.edition)
            print(f"\nfit: tighten={fit.tighten}  left={fit.left_bottom:.1f}pt  "
                  f"right={fit.right_bottom:.1f}pt  limit={render.BOTTOM_LIMIT_PT:.0f}pt")
        render.render_png(config, CONTENT, MARKET, DATE_LONG, out / "preview.png",
                          args.scale, args.edition)
    except render.RenderError as exc:
        print(f"\nRENDER FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"\noutput in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
