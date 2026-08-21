# News in 60 Seconds

A daily financial newsletter that writes and sends itself.

Every weekday morning at 9:30 Amman time, this thing wakes up, pulls live FX and
commodity prices, reads the news, writes a one-page briefing, lays it out as a
PDF that looks exactly like the original, and emails it out. Nobody touches it.

![the newsletter](reference/original-issue-2025-07-27.jpg)

## Where this came from

I spent about four weeks in the summer of 2025 interning at Capital Bank of
Jordan, rotating through Data Management, Treasury, and Financial Institutions.
Somewhere in the Treasury rotation the department head mentioned that nobody had
a daily market briefing anymore — there used to be a newsletter, it quietly died,
and now everyone just sort of… checked things themselves. Separately. Every
morning.

So I built one. The original ran on the OpenAI API for research and writing and a
pile of Google Apps Script for formatting and delivery. It got picked up by three
departments and then spread further inside the bank, which was genuinely the most
satisfying thing that happened to me that summer.

**This repo is a rebuild of that project** — same layout, same fonts, same
sections, rewritten properly with Claude doing the research and writing. It's a
portfolio piece, not the bank's live system. More on that below.

## What actually happens when it runs

```
market.py     →  yfinance: 6 FX pairs + gold, silver, Brent, WTI
                 computes the day's move, decides green/red/grey
                       ↓
research.py   →  Claude + web search: reads the last 24-48h, writes the copy
                 (gets the prices handed to it — it never invents a number)
                       ↓
render.py     →  Jinja2 → HTML → headless Chromium → a 612×792pt PDF
                 measures the result; if it overflows, tightens and retries
                       ↓
mailer.py     →  Gmail SMTP, PDF attached
```

If any step can't do its job properly, the whole run stops and emails me a
traceback instead. A market briefing that's confidently wrong is worse than no
market briefing.

## The layout

The original only survived as a PDF, so I reverse-engineered it. The background
graphics layer came out of the PDF as a PNG with no text on it, which meant I
could scan it programmatically and get every pill and panel's exact rectangle
rather than eyeballing pixels. The palette came out of a colour histogram of that
same layer:

| | |
|---|---|
| Navy | `#253745` |
| Up | `#9CD66A` fill, `#64BB6D` arrow |
| Down | `#E64D44` fill, `#6D130A` arrow |
| Flat | `#7F7F7F` fill, `#042433` bars |

Typography came out of the PDF's own CSS: 30.12pt for the wordmark, 17.88pt for
section headings, 11.28pt body on a **13.56pt** line, 12.24pt bullets on a
**14.64pt** line. Both work out to leading of almost exactly 1.20, paragraphs are
separated by one blank line, and bullets get no extra gap at all. I had guessed
1.28–1.30 at first and the left column ran straight off the bottom of the page.

The one thing I deliberately changed: the two dark panels in the original sit at
slightly different x positions and widths (338.5pt vs 329.5pt). That's almost
certainly a hand-placement accident rather than a design decision, so I
normalised them.

## Keeping it on one page

This is the part that would break first if I'd been lazy about it, because the
amount of news varies every single day. Three layers:

1. **Word budgets in the prompt.** Every section has a hard limit, and they're in
   `config.yaml` rather than buried in a string somewhere.
2. **A validator that counts words afterwards** and re-asks Claude with the
   specific overruns quoted back at it — "local #1: 81 words, limit 42, cut 39."
3. **A fit check in the renderer.** After laying the page out, it measures where
   the content actually ends. If anything crosses 762pt it re-renders at a
   tighter setting and tries again. It only ever shaves leading and gaps — it
   will never silently drop a bullet. If it genuinely can't fit, the run fails
   loudly.

## A thing I found out about yfinance

Worth writing down because it surprised me.

The four commodities come back perfect. `BZ=F` and `CL=F` reproduce the original
July 2025 issue *to the digit* — Brent 68.44 at -1.07%, WTI 65.16 at -1.32%.
There's a test for it (`tests/test_against_sample.py`) that asserts exactly this.

FX is a different story. Yahoo's `EURUSD=X`-style tickers return **one snapshot
per day with `open == close`** — they're not real session bars, and the snapshot
is taken at some loose hour that doesn't line up with any close you'd recognise.
GBP's row dated 2025-07-28 was 1.3440, which is basically the value the original
newsletter printed on Sunday the 27th. The bars are shifted by roughly a row.

CME FX futures (`6E=F`, `6B=F`, `6C=F`…) have genuine OHLC, and GBP/CAD/AUD land
within 0.04% of the original versus 0.43–0.53% for the `=X` tickers. But futures
carry basis: CHF sits about 0.71% off spot, which is ~57 pips, which an FX desk
would notice immediately.

So neither is strictly better and I wired up both. Spot is the default because
people reading an FX briefing expect spot rates and the page literally says "all
figures are indicative". Swapping the order of the `sources:` list in
`config.yaml` flips any instrument over to futures. Both the printed price and
the change always come from the same series, so they can never disagree with each
other.

The test reports the FX gap every run rather than asserting on it, because the
original's actual data source is long gone and pretending otherwise would be
fiction.

## Setting it up

You'll need Python 3.11+ (3.9 won't work — `yfinance` pulls a dependency that
needs 3.10 or newer).

```bash
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt
playwright install chromium
```

**Fonts.** Seravek is a commercial typeface, so the font files are gitignored and
this repo doesn't ship them. Drop `Seravek.ttf`, `Seravek-Light.ttf`, and
`Seravek-Bold.ttf` into `fonts/` and everything works. (Medium isn't used — the
page only ever asks for 300, 400 and 700.) The renderer takes either raw `.ttf`
or subsetted `.woff2`, so the same code path serves local dev and CI. If you
don't have Seravek, swap the `@font-face` block for something metrically similar
— Alegreya Sans or Source Sans 3 both get close.

**See it render** without spending a cent on API calls or emailing anyone:

```bash
python scripts/preview.py
```

That builds `out/preview.pdf` and `out/preview.png` from a fixture containing the
original July 2025 issue's exact content. It's also the loop I used while getting
the layout right.

**Environment variables** for a real run:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=...        # 16 chars, from Google, not your password
```

The Gmail one needs 2-Step Verification turned on, then an app password from
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
Yes, an app password rather than OAuth — I tried OAuth first and a personal
Google account's refresh token expires every 7 days while the consent screen is
in "Testing", which means an unattended cron job silently dies every week until
you notice. One secret that never expires beats that.

## Running it

```bash
python src/main.py --dry-run     # build the PDF, don't send it
python src/main.py               # for real
python src/main.py --force       # ignore the Sun–Thu schedule
python src/main.py --date 2026-07-30
```

It only publishes Sunday through Thursday, because that's Jordan's working week
and that's what the original did.

## Putting it on autopilot

`.github/workflows/daily.yml` runs it on GitHub Actions at `30 6 * * 0-4`, which
is 06:30 UTC — 09:30 in Amman. Jordan is UTC+3 all year (they abolished DST in
2022), so there's no daylight-saving drift to worry about, which is a nice change
from every other scheduling problem I've had.

Four repository secrets:

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `GMAIL_ADDRESS` | the sending address |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
| `SERAVEK_LIGHT_B64` | Seravek Light, subsetted |
| `SERAVEK_REGULAR_B64` | Seravek Regular, subsetted |
| `SERAVEK_BOLD_B64` | Seravek Bold, subsetted |

The three font secrets exist because of the licensing thing, and there are
*three* of them because of a limit I walked straight into: tarring all four TTFs
into one blob gives you 833 KB, and **a GitHub Actions secret caps at 48 KB**.

So `scripts/pack_fonts.py` subsets each weight down to the 334 characters the
page can actually print and converts to woff2. That's ~29 KB per weight, ~39 KB
once base64'd — comfortably under the cap. It also drops Medium entirely, which
the template never asked for and which I'd been embedding into every PDF for
nothing.

I checked this doesn't cost any fidelity: rendering from the subsetted woff2 and
from the original TTFs produces a **pixel-for-pixel identical page** — zero of
3.4 million pixels differ.

Run `python scripts/pack_fonts.py`, set the three secrets it prints the commands
for, then delete the `.b64.txt` files. CI restores them before rendering.

Optionally add a `RECIPIENTS` secret (comma-separated) if you'd rather not have
addresses sitting in `recipients.yaml` in a public repo.

Every issue also gets committed to `archive/` and uploaded as a build artifact,
so there's a browsable history of everything it's ever sent.

## Layout of the repo

```
config.yaml          # tickers, word budgets, geometry, palette, schedule
recipients.yaml      # who gets it
src/
  market.py          # prices and the up/down/flat logic. no LLM anywhere near it
  research.py        # Claude + web search → typed JSON, budget-enforced
  render.py          # HTML → PDF, plus the one-page fit check
  mailer.py          # SMTP, and the "it broke" alert
  main.py            # orchestration and the failure policy
templates/
  newsletter.html.j2 # the page
scripts/
  preview.py         # render from the fixture, no API calls
  pack_fonts.py      # fonts → secret
  unpack_fonts.py    # secret → fonts (CI runs this)
tests/
  test_against_sample.py    # checks the maths against the real July 2025 issue
  fixture_original_issue.py # that issue's exact content
reference/           # the original PDF export and render
```

## Two honest notes

**The branding.** This carries Capital Bank of Jordan's logo and a disclaimer
written in the bank's voice, because keeping it faithful was the whole point. It
is not an official bank publication and it's not the system the bank runs — it's
my reconstruction of a project I built there. Keep `recipients.yaml` to people
who know that.

**The Buffett quote.** The original issue has him saying returns *increase* as
motion increases. He said **decrease** — the whole point of the line is that
trading more makes you poorer. I've left the original render untouched in
`reference/` but the pipeline quotes things correctly going forward.
