"""The ledger of past editions: what number each issue carried, and its quote.

The pipeline is otherwise stateless — every run starts from the market and a
blank page. Two things need memory, though: an edition number has to keep
counting, and a quote the newsletter has already printed should not come round
again. Both live in one small JSON file.

It sits inside the archive directory on purpose. The GitHub Actions workflow
already commits `archive/` back to the repo after a successful run, so the
ledger survives a checkout that starts fresh every morning. Put it anywhere
else and CI silently forgets every edition the moment the runner is torn down.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATE_NAME = "history.json"

# How many past quotes to hand Claude. The ledger grows forever; the prompt
# should not. Recent ones are what a reader would notice repeating.
PROMPT_RECENT = 40


def path_for(root: Path, config: dict[str, Any]) -> Path:
    archive_dir = config.get("archive", {}).get("dir", "archive")
    return root / archive_dir / STATE_NAME


def load(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Read the ledger. A missing or unreadable file is an empty ledger.

    This is deliberately forgiving: a corrupt ledger should cost us de-duplication
    for one morning, not the whole issue.
    """
    p = path_for(root, config)
    if not p.exists():
        return {"editions": []}

    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("  history at %s is unreadable (%s) — starting empty", p, exc)
        return {"editions": []}

    if not isinstance(state, dict) or not isinstance(state.get("editions"), list):
        log.warning("  history at %s has an unexpected shape — starting empty", p)
        return {"editions": []}

    # Ledgers written before `retired` existed carry their quotes only inside
    # `editions`. Seed from there once so nothing already published comes back.
    if not isinstance(state.get("retired"), list):
        state["retired"] = [
            {"text": q.get("text", ""), "author": q.get("author", ""),
             "date": e.get("date", "")}
            for e in state["editions"]
            for q in [e.get("quote") or {}]
            if (q.get("text") or "").strip()
        ]

    return state


def normalise(text: str) -> str:
    """Compare quotes on their words alone — punctuation and case drift."""
    kept = [c for c in text.lower() if c.isalnum() or c.isspace()]
    return " ".join("".join(kept).split())


def _retired(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Every quote ever sent, newest last. Append-only.

    This is deliberately separate from `editions`, which holds one entry per
    date and gets rewritten when a date is re-run. A re-run replaces that
    date's edition record, so on its own `editions` would forget the quote the
    superseded run had already mailed out — and hand it back the next morning.
    Nothing is ever removed from here.
    """
    got = state.get("retired")
    return got if isinstance(got, list) else []


def used_quotes(state: dict[str, Any]) -> list[str]:
    """Every quote the newsletter has printed, newest first."""
    seen, out = set(), []
    for quote in reversed(_retired(state)):
        text = (quote.get("text") or "").strip()
        key = normalise(text)
        if not text or key in seen:
            continue
        seen.add(key)
        author = (quote.get("author") or "").strip()
        out.append(f"{text} — {author}" if author else text)
    return out


def is_used(state: dict[str, Any], text: str) -> bool:
    if not text.strip():
        return False
    target = normalise(text)
    return any(normalise(q.get("text") or "") == target for q in _retired(state))


def edition_for(state: dict[str, Any], iso: str, start: int = 1) -> int:
    """The number this issue carries.

    Re-running a date reuses its number rather than burning a new one, so a
    failed send followed by a retry doesn't leave a hole in the sequence.
    """
    editions = state.get("editions", [])
    for entry in editions:
        if entry.get("date") == iso:
            return int(entry["edition"])

    numbers = [int(e["edition"]) for e in editions if "edition" in e]
    return max(numbers) + 1 if numbers else start


def record(root: Path, config: dict[str, Any], state: dict[str, Any],
           iso: str, edition: int, quote: dict[str, Any]) -> None:
    """Write this issue into the ledger.

    The edition entry for `iso` is replaced; the quote is appended to `retired`
    and never removed. Re-running a date therefore keeps its number stable while
    still remembering every quote that actually went out.
    """
    text = (quote or {}).get("text", "")
    author = (quote or {}).get("author", "")
    entry = {"date": iso, "edition": int(edition),
             "quote": {"text": text, "author": author}}

    editions = [e for e in state.get("editions", []) if e.get("date") != iso]
    editions.append(entry)
    editions.sort(key=lambda e: e.get("date", ""))
    state["editions"] = editions

    retired = _retired(state)
    if text.strip() and not is_used(state, text):
        retired = [*retired, {"text": text, "author": author, "date": iso}]
    state["retired"] = retired

    p = path_for(root, config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("  recorded edition No. %d in %s", edition, p.name)
