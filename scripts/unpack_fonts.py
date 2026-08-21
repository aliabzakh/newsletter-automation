"""Restore fonts/*.woff2 from the per-weight secrets. Run by CI before rendering.

Counterpart to pack_fonts.py. Three secrets, one per weight, because a single
combined blob would blow past GitHub's 48 KB per-secret limit.
"""

from __future__ import annotations

import base64
import binascii
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "fonts"

SECRETS = {
    "SERAVEK_LIGHT_B64": "Seravek-Light.woff2",
    "SERAVEK_REGULAR_B64": "Seravek.woff2",
    "SERAVEK_BOLD_B64": "Seravek-Bold.woff2",
}


def main() -> None:
    missing = [name for name in SECRETS if not os.environ.get(name, "").strip()]
    if missing:
        sys.exit(
            f"missing font secret(s): {', '.join(missing)}\n"
            "Generate them locally with `python scripts/pack_fonts.py`, then add each\n"
            "as a repository secret. See the Fonts section of the README."
        )

    FONT_DIR.mkdir(exist_ok=True)
    for name, filename in SECRETS.items():
        blob = os.environ[name].strip()
        try:
            raw = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError) as exc:
            sys.exit(f"{name} is not valid base64: {exc}")
        # woff2 files start with the signature 'wOF2'.
        if raw[:4] != b"wOF2":
            sys.exit(f"{name} did not decode to a woff2 file (got {raw[:4]!r})")
        (FONT_DIR / filename).write_bytes(raw)
        print(f"  restored fonts/{filename}  ({len(raw) / 1024:.1f} KB)")

    print(f"fonts restored to {FONT_DIR}")


if __name__ == "__main__":
    main()
