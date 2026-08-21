"""The writing half of the newsletter: Claude + web search.

One Messages API call does the whole job — Claude searches the web, then returns
the day's copy as JSON matching a fixed schema. The prices it writes *about*
come from market.py and are handed in as text; Claude is explicitly told not to
invent or contradict them.

Two things keep the output on one page:

  1. The prompt states a word budget per section, derived from config.yaml.
  2. `validate()` counts the words afterwards. If Claude overran, we re-ask with
     the specific overruns quoted back at it. Only after `budget_retries` failed
     attempts does the run give up — and even then it hands the copy to the
     renderer, whose fit check is the real backstop.

The schema deliberately omits `minItems` / `maxItems`: the structured-outputs
JSON Schema subset doesn't support array-length constraints, so counts are
enforced in `validate()` rather than silently dropped by the API.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

import history

log = logging.getLogger(__name__)


class ResearchError(RuntimeError):
    """Raised when Claude can't produce usable copy. Always fatal."""


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "quote": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "author": {"type": "string"},
            },
            "required": ["text", "author"],
            "additionalProperties": False,
        },
        "forex": {"type": "array", "items": {"type": "string"}},
        "international": {"type": "array", "items": {"type": "string"}},
        "local": {"type": "array", "items": {"type": "string"}},
        "on_this_day": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "year": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["year", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["quote", "forex", "international", "local", "on_this_day"],
    "additionalProperties": False,
}

SYSTEM = """\
You write "News in 60 Seconds", the daily one-page market briefing published by \
Capital Bank of Jordan's Treasury desk. Your readers are bankers: treasury, \
financial institutions, and corporate coverage staff in Amman.

House style:
- Terse, factual, wire-service register. No hedging, no filler, no preamble.
- Name concrete numbers, institutions, and dates. "Brent settled -1.07% at $68.44" \
beats "oil fell somewhat".
- No markdown, no bullet characters, no bold, no headings — the layout supplies all \
of that. Return plain sentences only.
- Never open a bullet with "Additionally", "Moreover", or "In other news".
- Write in English. Use $ for USD and JD for Jordanian dinar.

Hard rules on numbers:
- The market table below is authoritative and already rendered on the page. Any \
price you mention in prose MUST match it. Do not restate a rate with different \
digits, and do not invent prices for instruments that aren't listed.
- If you cannot verify a figure through search, describe the move qualitatively \
instead of guessing a number.
- Never fabricate a headline, a source, or an institution's forecast.
- FX direction is not the same as the number's direction. Half the table is \
printed as USD per unit, where the pair rising means the DOLLAR strengthened and \
that currency weakened. Each row spells out which way round it is — read the \
gloss and follow it rather than assuming a higher number means a stronger \
currency.
"""

PROMPT = """\
Today is {date_long} ({weekday}). Write today's issue.

The market table on the page shows these figures, captured around the most \
recent close. Your prose must be consistent with them:

{market_block}

Research the last 24-48 hours using web search, then write these five sections.

1. `forex` — exactly {forex_count} paragraphs, at most {forex_words} words each.
   The FX narrative: what the dollar did and why, which pairs moved, what the \
week's central-bank and data calendar implies. Reference the pairs in the table \
by name. Paragraph 1 should lead with the dollar's overall tone.

2. `international` — exactly {intl_count} bullets, at most {intl_words} words each.
   Global and regional market news: energy, metals, equities, central banks, \
major bank or institution forecasts, trade policy. One self-contained fact per \
bullet.

3. `local` — exactly {local_count} bullets, at most {local_words} words each.
   Jordan specifically. Search Central Bank of Jordan releases, the Department of \
Statistics, Jordan News Agency (Petra), and the Amman Stock Exchange. Prefer hard \
indicators: inflation, GDP, foreign reserves, current account, exports, ASE \
performance, banking-sector figures, sovereign ratings. If genuinely nothing new \
broke, report the latest published figures and label the reference period.

4. `on_this_day` — exactly {otd_count} entries.
   Notable events in financial history that happened on {month_day}, any year. \
`year` is the four-digit year as a string; `text` is at most {otd_words} words \
explaining what happened and why it mattered. Prefer genuinely significant events \
— central bank foundings, market crashes, landmark deals, major corporate results.

5. `quote` — one finance or investing quote, at most {quote_words} words, plus its \
author. Rotate: do not pick the most obvious Buffett line. Quote it accurately; if \
you are unsure of the exact wording, choose a different quote you are sure of.
{quote_history}
Word limits are hard. The page is a fixed single page and overlong copy breaks \
the layout.
"""

# Slotted into the prompt above when the ledger has anything in it. Kept out of
# the main body so a first-ever run doesn't carry a dangling empty heading.
QUOTE_HISTORY_BLOCK = """
The newsletter has already printed the quotes below. Pick something else — a \
different author, not just a different line from the same one:

{quotes}
"""


def _usd_base(cfg: dict[str, Any]) -> dict[str, bool]:
    """Which FX rows print as USD per unit, keyed by label.

    Taken from each row's first source, which is what fixes the printed
    orientation — the `invert` flag on the fallbacks exists precisely to make
    them agree with it. USDCHF=X prints dollars per franc; EURUSD=X prints
    dollars per euro the other way up.
    """
    out = {}
    for row in cfg.get("currencies", []):
        sources = row.get("sources") or [{}]
        symbol = str(sources[0].get("symbol", ""))
        out[row["label"]] = symbol.upper().startswith("USD")
    return out


def _market_block(market: dict[str, Any], cfg: dict[str, Any]) -> str:
    lines = []
    arrow = {"up": "up", "down": "down", "flat": "unchanged"}
    usd_base = _usd_base(cfg)

    for q in market["currencies"]:
        # Spell out what the move means, so the copy can't invert it.
        if q.direction == "flat":
            gloss = "unchanged"
        elif usd_base.get(q.label):
            gloss = ("USD stronger, {c} weaker" if q.direction == "up"
                     else "USD weaker, {c} stronger").format(c=q.label)
        else:
            gloss = ("{c} stronger, USD weaker" if q.direction == "up"
                     else "{c} weaker, USD stronger").format(c=q.label)

        pair = f"USD/{q.label}" if usd_base.get(q.label) else f"{q.label}/USD"
        lines.append(
            f"  {q.label}: {q.price_text}  ({pair}, {q.change_text} bps "
            f"{arrow[q.direction]} = {gloss})"
        )
    for q in market["commodities"]:
        lines.append(
            f"  {q.label}: {q.price_text}  ({q.change_text}% {arrow[q.direction]})"
        )
    lines.append(f"  Session: {market['as_of'].isoformat()}")
    return "\n".join(lines)


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def validate(content: dict[str, Any], budgets: dict[str, Any],
             state: dict[str, Any] | None = None) -> list[str]:
    """Return a list of human-readable budget violations (empty means it fits).

    `state` is the edition ledger, when there is one. A repeated quote is
    reported here rather than anywhere else so it rides the same retry loop as
    an over-long bullet — Claude gets told what's wrong and fixes it in place.
    """
    problems: list[str] = []

    def check_list(key: str, spec: dict[str, Any], label: str, getter=lambda x: x) -> None:
        items = content.get(key) or []
        want = spec["count"]
        if len(items) != want:
            problems.append(f"{label}: got {len(items)} items, need exactly {want}.")
        limit = spec["words_each"]
        for i, item in enumerate(items, 1):
            n = _words(getter(item))
            if n > limit:
                problems.append(
                    f"{label} #{i}: {n} words, limit {limit}. Cut {n - limit} words."
                )

    check_list("forex", budgets["forex_paragraphs"], "forex")
    check_list("international", budgets["international_bullets"], "international")
    check_list("local", budgets["local_bullets"], "local")
    check_list(
        "on_this_day",
        budgets["on_this_day_bullets"],
        "on_this_day",
        getter=lambda d: d.get("text", ""),
    )

    qmax = budgets["quote"]["max_words"]
    qtext = (content.get("quote") or {}).get("text", "")
    if _words(qtext) > qmax:
        problems.append(f"quote: {_words(qtext)} words, limit {qmax}.")

    if state is not None and history.is_used(state, qtext):
        problems.append(
            "quote: this one has run in an earlier edition. Pick a different "
            "quote, by a different author."
        )

    return problems


def _extract_json(response: Any) -> dict[str, Any]:
    """Pull the JSON payload out of the response's text blocks."""
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise ResearchError(
            "Claude returned no text block — the turn produced only tool calls. "
            f"stop_reason={response.stop_reason}"
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResearchError(f"response was not valid JSON: {exc}\n{text[:400]}") from exc


def _call(client: Any, cfg: dict[str, Any], messages: list[dict[str, Any]]) -> Any:
    """One Messages request, resuming through any server-tool pauses."""
    research = cfg["research"]
    tools = [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": research["max_web_searches"],
        }
    ]

    for attempt in range(5):
        response = client.messages.create(
            model=research["model"],
            max_tokens=research["max_tokens"],
            system=SYSTEM,
            tools=tools,
            output_config={
                "effort": research.get("effort", "medium"),
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            messages=messages,
        )

        # Safety classifiers can decline; check before touching content.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            raise ResearchError(f"request refused by safety classifiers (category={category})")

        if response.stop_reason == "max_tokens":
            raise ResearchError(
                f"hit max_tokens ({research['max_tokens']}) before finishing — "
                "raise research.max_tokens in config.yaml"
            )

        # The server-side search loop hit its iteration cap; re-send to resume.
        if response.stop_reason == "pause_turn":
            log.info("  server tool paused, resuming (%d)", attempt + 1)
            messages = [*messages, {"role": "assistant", "content": response.content}]
            continue

        searches = sum(1 for b in response.content if b.type == "server_tool_use")
        log.info(
            "  %d web searches, %d in / %d out tokens",
            searches,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return response

    raise ResearchError("server tool never finished after 5 resume attempts")


def generate(cfg: dict[str, Any], market: dict[str, Any], today: date,
             date_long: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Research and write today's copy. Raises ResearchError if it can't.

    `state` is the edition ledger. Pass it and past quotes are both kept out of
    the prompt and rejected in validation if one comes back anyway.
    """
    import anthropic

    budgets = cfg["budgets"]
    client = anthropic.Anthropic()

    weekday = today.strftime("%A")

    past = history.used_quotes(state) if state else []
    if past:
        shown = past[: history.PROMPT_RECENT]
        quote_history = QUOTE_HISTORY_BLOCK.format(
            quotes="\n".join(f"- {q}" for q in shown)
        )
        log.info("  %d quote(s) on the do-not-repeat list", len(past))
    else:
        quote_history = ""

    prompt = PROMPT.format(
        quote_history=quote_history,
        date_long=date_long,
        weekday=weekday,
        # %-d is glibc-only and blows up on Windows; build it by hand.
        month_day=f"{today:%B} {today.day}",
        market_block=_market_block(market, cfg),
        forex_count=budgets["forex_paragraphs"]["count"],
        forex_words=budgets["forex_paragraphs"]["words_each"],
        intl_count=budgets["international_bullets"]["count"],
        intl_words=budgets["international_bullets"]["words_each"],
        local_count=budgets["local_bullets"]["count"],
        local_words=budgets["local_bullets"]["words_each"],
        otd_count=budgets["on_this_day_bullets"]["count"],
        otd_words=budgets["on_this_day_bullets"]["words_each"],
        quote_words=budgets["quote"]["max_words"],
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    attempts = int(cfg["research"].get("budget_retries", 2)) + 1
    content: dict[str, Any] = {}

    for attempt in range(1, attempts + 1):
        log.info("research attempt %d/%d", attempt, attempts)
        response = _call(client, cfg, messages)
        content = _extract_json(response)

        problems = validate(content, budgets, state)
        if not problems:
            log.info("  copy fits every budget")
            return content

        log.warning("  %d budget problem(s):", len(problems))
        for p in problems:
            log.warning("    %s", p)

        if attempt == attempts:
            # The renderer's fit check is the real backstop — let it try.
            log.warning("  out of retries; handing over-length copy to the renderer")
            return content

        messages = [
            *messages,
            {"role": "assistant", "content": json.dumps(content)},
            {
                "role": "user",
                "content": (
                    "That copy breaks the layout. Fix exactly these problems and "
                    "return the corrected JSON — keep everything else identical, "
                    "and do not run any more searches:\n\n"
                    + "\n".join(f"- {p}" for p in problems)
                ),
            },
        ]

    return content


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys
    from pathlib import Path

    import yaml

    sys.path.insert(0, str(Path(__file__).parent))
    import market as market_mod  # noqa: E402

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(__file__).parent.parent
    config = yaml.safe_load((root / "config.yaml").read_text("utf-8"))

    data = market_mod.fetch(config)
    today = date.today()
    out = generate(config, data, today, today.strftime("%B %d, %Y").replace(" 0", " "))
    print(json.dumps(out, indent=2, ensure_ascii=False))
