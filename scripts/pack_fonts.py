"""Subset the Seravek fonts and emit one base64 blob per weight, for CI secrets.

The fonts are gitignored (commercially licensed), so CI needs another way to get
them. The obvious approach — tar all four TTFs into one secret — does not work:
that blob is 833 KB and a GitHub Actions secret caps at 48 KB.

So this subsets each weight to the characters the newsletter can actually print
and converts to woff2, which lands each weight around 40 KB of base64. One
secret per weight, three weights (the page never uses Medium).

    python scripts/pack_fonts.py

Writes fonts/*.woff2 plus one .b64.txt per weight, and prints the commands to
set them. Delete the .b64.txt files afterwards — they are the fonts in another
costume, and .gitignore covers them but only if you don't rename them.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "fonts"

# weight -> (source stem, secret name). Medium is deliberately absent: the
# template only ever asks for 300, 400 and 700.
WEIGHTS = {
    300: ("Seravek-Light", "SERAVEK_LIGHT_B64"),
    400: ("Seravek", "SERAVEK_REGULAR_B64"),
    700: ("Seravek-Bold", "SERAVEK_BOLD_B64"),
}

GITHUB_SECRET_LIMIT = 48 * 1024


def charset() -> list[int]:
    """Every character the page can plausibly print."""
    chars = {chr(c) for c in range(0x20, 0x7F)}          # ASCII printable
    chars |= {chr(c) for c in range(0xA0, 0x180)}        # Latin-1 + Latin Extended-A
    chars |= set("‘’“”–—•…€£¥₪′″≈≤≥±×÷°")               # smart punctuation, symbols
    return sorted(ord(c) for c in chars)


def main() -> None:
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError:
        sys.exit("pip install fonttools brotli")

    missing = [s for s, _ in WEIGHTS.values() if not (FONT_DIR / f"{s}.ttf").exists()]
    if missing:
        sys.exit(f"missing in {FONT_DIR}: {', '.join(n + '.ttf' for n in missing)}")

    unicodes = charset()
    print(f"subsetting to {len(unicodes)} characters\n")
    commands, oversize = [], []

    for weight, (stem, secret) in sorted(WEIGHTS.items()):
        src = FONT_DIR / f"{stem}.ttf"
        font = TTFont(str(src))

        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern", "liga", "calt", "onum", "tnum"]
        opts.desubroutinize = True
        opts.notdef_outline = True
        subsetter = subset.Subsetter(options=opts)
        subsetter.populate(unicodes=unicodes)
        subsetter.subset(font)

        buf = io.BytesIO()
        font.flavor = "woff2"
        font.save(buf)
        raw = buf.getvalue()
        (FONT_DIR / f"{stem}.woff2").write_bytes(raw)

        blob = base64.b64encode(raw).decode("ascii")
        out = ROOT / f"{secret}.b64.txt"
        out.write_text(blob, encoding="ascii")

        fits = len(blob) <= GITHUB_SECRET_LIMIT
        status = "ok" if fits else "TOO BIG"
        if not fits:
            oversize.append(secret)
        print(f"  {weight:>3}  {stem:<16} {src.stat().st_size / 1024:7.1f} KB -> "
              f"{len(raw) / 1024:5.1f} KB woff2 -> {len(blob) / 1024:5.1f} KB base64  {status}")
        commands.append(f"  gh secret set {secret} < {out.name}")

    if oversize:
        sys.exit(f"\n{', '.join(oversize)} exceed the 48 KB secret limit — trim the charset.")

    print("\nSet these three secrets (bash / git-bash):")
    print("\n".join(commands))
    print("\nPowerShell has no `<` redirection — pipe instead:")
    for _, secret in sorted(WEIGHTS.values()):
        print(f"  Get-Content {secret}.b64.txt -Raw | gh secret set {secret}")
    print("\nThen delete the .b64.txt files.")


if __name__ == "__main__":
    main()
