"""Meeting context file: free-form Markdown plus an optional keyterm list."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from meeting_assist.llm import LLM, Message

log = logging.getLogger(__name__)

MAX_KEYTERMS = 100
MAX_KEYTERM_LEN = 50  # AssemblyAI: each term must be under 50 characters

_KEYTERMS_HEADING = re.compile(r"^##\s*keyterms\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")


@dataclass(frozen=True)
class MeetingContext:
    text: str
    keyterms: list[str]
    prompt: str  # short line handed to the recogniser as its prompt

    @staticmethod
    def empty() -> MeetingContext:
        return MeetingContext(text="", keyterms=[], prompt="")


def parse_keyterms_section(text: str) -> list[str] | None:
    """Bullets under a `## Keyterms` heading, or None when there is no such section."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _KEYTERMS_HEADING.match(line):
            terms: list[str] = []
            for rest in lines[i + 1 :]:
                if _HEADING.match(rest):
                    break
                m = _BULLET.match(rest)
                if m:
                    terms.append(m.group(1))
            return terms
    return None


def summary_prompt(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if first.startswith("#"):
        return first.lstrip("#").strip()
    return text.strip()[:200]


def clean_keyterms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        t = term.strip()
        if not t or len(t) >= MAX_KEYTERM_LEN or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
        if len(out) == MAX_KEYTERMS:
            break
    return out


def load_context(path: Path) -> MeetingContext:
    text = Path(path).read_text(encoding="utf-8")
    section = parse_keyterms_section(text)
    return MeetingContext(
        text=text,
        keyterms=clean_keyterms(section) if section is not None else [],
        prompt=summary_prompt(text),
    )


_DERIVE_SYSTEM = (
    "You extract vocabulary for a speech recogniser from a meeting briefing. "
    "Return the terms it is most likely to get wrong: people's names, company "
    "and product names, acronyms, project code names, and domain jargon, as "
    "they would be spoken. At most 100 terms, each under 50 characters. Do not "
    "include common English words. Output one term per line and nothing else: "
    "no bullets, no numbering, no commentary."
)


def parse_keyterm_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        term = _LIST_PREFIX.sub("", line).strip()
        if term:
            out.append(term)
    return out


def derive_keyterms(text: str, llm: LLM) -> list[str]:
    """Ask the LLM for keyterms. Any failure logs a warning and yields none:
    transcription still works without keyterms."""
    try:
        reply = llm.complete(_DERIVE_SYSTEM, [Message("user", text)], max_tokens=2000)
    except Exception as exc:
        log.warning("keyterm derivation failed: %s", exc)
        return []
    return clean_keyterms(parse_keyterm_lines(reply))
