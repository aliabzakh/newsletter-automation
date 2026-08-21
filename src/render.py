"""Turn content + prices into the one-page PDF.

Jinja2 builds the HTML, Chromium prints it. The interesting part is the fit
check: a daily newsletter whose content length varies will eventually overflow,
and a two-page "one-page newsletter" is the failure everyone notices. So after
rendering we measure the real laid-out geometry in the browser and, if anything
overruns, re-render at a tighter setting.

Tightening only ever adjusts leading and gaps. It never drops a bullet — if the
copy genuinely cannot fit, that's a research-side bug and the run should fail
loudly rather than silently publish a truncated page.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"
FONT_DIR = ROOT / "fonts"

# Seravek weight -> filename stem. Medium is deliberately absent: the template
# only ever asks for 300, 400 and 700, so embedding it just bloated every PDF.
# Each weight resolves to a subsetted .woff2 if present (what CI restores from
# secrets), otherwise the raw .ttf (what you have locally).
FONT_STEMS = {300: "Seravek-Light", 400: "Seravek", 700: "Seravek-Bold"}
FONT_FORMATS = [(".woff2", "woff2"), (".ttf", "truetype")]

MAX_TIGHTEN = 4          # steps the fit check may take before giving up
BOTTOM_LIMIT_PT = 762.0  # content must clear the disclaimer strip


class RenderError(RuntimeError):
    """Raised when the page cannot be made to fit. Always fatal."""


@dataclass
class Fit:
    ok: bool
    tighten: int
    left_bottom: float
    right_bottom: float

    @property
    def worst(self) -> float:
        return max(self.left_bottom, self.right_bottom)


LAUNCH_ATTEMPTS = 3      # Chromium's first start can lose a race with AV scanning
LAUNCH_BACKOFF_S = 2.0


def _launch_chromium(pw: Any) -> Any:
    """Start headless Chromium, retrying a transient failure to reach the binary.

    Windows anti-virus occasionally holds a lock on the 200MB headless shell the
    first time it is touched after a reboot, and Playwright reports that as
    "Executable doesn't exist". Retrying costs two seconds; not retrying costs
    the whole issue, including the research call that produced it.
    """
    from playwright.sync_api import Error as PlaywrightError

    last: Exception | None = None
    for attempt in range(1, LAUNCH_ATTEMPTS + 1):
        try:
            return pw.chromium.launch()
        except PlaywrightError as exc:
            last = exc
            if attempt < LAUNCH_ATTEMPTS:
                log.warning("  chromium launch attempt %d/%d failed: %s",
                            attempt, LAUNCH_ATTEMPTS, str(exc).splitlines()[0])
                time.sleep(LAUNCH_BACKOFF_S)

    raise RenderError(
        f"could not start Chromium after {LAUNCH_ATTEMPTS} attempts: {last}. "
        "Reinstall the browser with: python -m playwright install chromium"
    ) from last


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _arrow_svg(direction: str, palette: dict[str, str]) -> Markup:
    """The glyph inside a pill: triangle up, triangle down, or equals bars.

    Returned as Markup so Jinja's autoescape leaves the SVG alone — the colours
    interpolated in come from config, not from anything Claude or the web wrote.
    """
    if direction == "up":
        svg = (
            f'<svg width="24" height="17" viewBox="0 0 24 17">'
            f'<polygon points="12,1 23,16 1,16" fill="{palette["up_arrow"]}"/></svg>'
        )
    elif direction == "down":
        svg = (
            f'<svg width="24" height="17" viewBox="0 0 24 17">'
            f'<polygon points="12,16 1,1 23,1" fill="{palette["down_arrow"]}"/></svg>'
        )
    else:
        svg = (
            f'<svg width="24" height="17" viewBox="0 0 24 17">'
            f'<rect x="2" y="4"  width="20" height="3.4" rx="1.2" fill="{palette["flat_bar"]}"/>'
            f'<rect x="2" y="10" width="20" height="3.4" rx="1.2" fill="{palette["flat_bar"]}"/></svg>'
        )
    return Markup(svg)


def _load_fonts() -> list[dict[str, Any]]:
    faces, missing = [], []
    for weight, stem in sorted(FONT_STEMS.items()):
        for ext, css_format in FONT_FORMATS:
            path = FONT_DIR / f"{stem}{ext}"
            if path.exists():
                faces.append({"weight": weight, "format": css_format,
                              "mime": "font/woff2" if ext == ".woff2" else "font/ttf",
                              "b64": _b64(path)})
                break
        else:
            missing.append(f"{stem}.woff2 or {stem}.ttf")

    if missing:
        raise RenderError(
            f"missing fonts in {FONT_DIR}: {', '.join(missing)}. "
            "Run `python scripts/unpack_fonts.py` (CI) or drop the Seravek TTFs in "
            "fonts/ (local). See the README."
        )
    return faces


def capture_note(config: dict[str, Any], market: dict[str, Any]) -> str:
    """The italic line under the timestamp.

    Fixed wording from config with the session date dropped in — the layout
    gives it about 95 characters before it wraps into the pills, which is too
    tight a target to leave to prose written fresh each morning.
    """
    d = market["as_of"]
    # %-d is glibc-only and blows up on Windows; build the day by hand.
    session = f"{d:%A}, {d.day} {d:%B %Y}"
    return config["masthead"]["capture_note"].format(session=session)


def build_html(config: dict[str, Any], content: dict[str, Any],
               market: dict[str, Any], date_long: str, tighten: int = 0,
               edition: int = 1) -> str:
    """Render the template to an HTML string."""
    palette = config["layout"]["palette"]

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,   # a typo'd variable should explode, not vanish
        autoescape=True,
    )
    template = env.get_template("newsletter.html.j2")

    logo_path = ROOT / config["masthead"]["logo"]
    if not logo_path.exists():
        raise RenderError(f"logo not found at {logo_path}")

    return template.render(
        layout=config["layout"],
        masthead=config["masthead"],
        content=content,
        currencies=market["currencies"],
        commodities=market["commodities"],
        date_long=date_long,
        edition=edition,
        capture_note=capture_note(config, market),
        tighten=tighten,
        fonts=_load_fonts(),
        logo_b64=_b64(logo_path),
        arrow=lambda d: _arrow_svg(d, palette),
    )


# The columns are absolutely positioned, so their own boxes report the true
# content extent. Anything past BOTTOM_LIMIT_PT collides with the disclaimer.
_MEASURE_JS = """
() => {
  const px2pt = 72 / 96;
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    return (r.top + r.height) * px2pt;
  };
  return { left: box('#col-left'), right: box('#col-right'),
           scroll: document.documentElement.scrollHeight * px2pt };
}
"""


def render_pdf(config: dict[str, Any], content: dict[str, Any], market: dict[str, Any],
               date_long: str, out_path: Path, edition: int = 1) -> Fit:
    """Render to `out_path`, tightening until the page fits. Raises if it can't."""
    from playwright.sync_api import sync_playwright

    page_cfg = config["layout"]["page"]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page(
                viewport={"width": int(page_cfg["width"] * 96 / 72),
                          "height": int(page_cfg["height"] * 96 / 72)}
            )

            fit = None
            for tighten in range(MAX_TIGHTEN + 1):
                html = build_html(config, content, market, date_long, tighten, edition)
                page.set_content(html, wait_until="load")
                page.wait_for_timeout(120)  # let embedded fonts settle before measuring

                m = page.evaluate(_MEASURE_JS)
                fit = Fit(
                    ok=m["left"] <= BOTTOM_LIMIT_PT and m["right"] <= BOTTOM_LIMIT_PT,
                    tighten=tighten,
                    left_bottom=m["left"],
                    right_bottom=m["right"],
                )

                if fit.ok:
                    if tighten:
                        log.info("  fits at tighten=%d (lowest content %.1fpt)", tighten, fit.worst)
                    else:
                        log.info("  fits at natural spacing (lowest content %.1fpt)", fit.worst)
                    break

                log.warning(
                    "  overflow at tighten=%d: left=%.1fpt right=%.1fpt (limit %.0fpt)",
                    tighten, fit.left_bottom, fit.right_bottom, BOTTOM_LIMIT_PT,
                )

            if fit is None or not fit.ok:
                raise RenderError(
                    f"content still overflows at tighten={MAX_TIGHTEN} "
                    f"(lowest content {fit.worst:.1f}pt vs {BOTTOM_LIMIT_PT:.0f}pt limit). "
                    "The copy is too long — tighten the budgets in config.yaml."
                )

            # page.pdf takes px/in/cm/mm, not pt — hand it inches.
            page.pdf(
                path=str(out_path),
                width=f"{page_cfg['width'] / 72:.4f}in",
                height=f"{page_cfg['height'] / 72:.4f}in",
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            browser.close()

    log.info("  wrote %s (%.0f KB)", out_path.name, out_path.stat().st_size / 1024)
    return fit


def render_png(config: dict[str, Any], content: dict[str, Any], market: dict[str, Any],
               date_long: str, out_path: Path, scale: int = 2,
               edition: int = 1) -> None:
    """Screenshot the page. Used for eyeballing the layout against the original."""
    from playwright.sync_api import sync_playwright

    page_cfg = config["layout"]["page"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = build_html(config, content, market, date_long, edition=edition)

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            page = browser.new_page(
                viewport={"width": int(page_cfg["width"] * 96 / 72),
                          "height": int(page_cfg["height"] * 96 / 72)},
                device_scale_factor=scale,
            )
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(120)
            page.screenshot(path=str(out_path), full_page=False)
        finally:
            browser.close()
    log.info("  wrote %s", out_path.name)
