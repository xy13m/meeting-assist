"""The transcript.jsonl record format and the Markdown rendering of turns.

This is the contract with the Claude Code skill, which greps the file for
the literal `"type": "turn"`: records are encoded with json.dumps defaults
(a space after each colon). Changing a record shape means updating
`skill/SKILL.md` and CLAUDE.md as well.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from meeting_assist.events import FinalTurn, SpeakerRevision, Translation

FORMAT_VERSION = 1


def encode(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False) + "\n"


def read_records(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def meta_record(started_at: datetime, target: str, languages: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "meta",
        "version": FORMAT_VERSION,
        "started_at": started_at.isoformat(),
        "target": target,
        "languages": list(languages),
    }


def turn_record(turn: FinalTurn) -> dict[str, Any]:
    return {
        "type": "turn",
        "turn_order": turn.turn_order,
        "speaker": turn.speaker,
        "text": turn.text,
        "started_at": turn.started_at.isoformat(),
        "ended_at": turn.ended_at.isoformat(),
        "language": turn.language,
    }


def translation_record(tr: Translation) -> dict[str, Any]:
    return {"type": "translation", "turn_order": tr.turn_order, "text": tr.text, "error": tr.error}


def revision_record(rev: SpeakerRevision) -> dict[str, Any]:
    return {"type": "revision", "turn_order": rev.turn_order, "speaker": rev.speaker}


@dataclass(frozen=True)
class Entry:
    """One turn with its translation (if any), speaker label already revised."""

    turn: FinalTurn
    translation: Translation | None


def assemble(records: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[Entry]]:
    """Rebuild the ordered transcript from raw records."""
    meta: dict[str, Any] | None = None
    turns: dict[int, FinalTurn] = {}
    translations: dict[int, Translation] = {}
    revisions: dict[int, str] = {}
    for rec in records:
        kind = rec.get("type")
        if kind == "meta":
            meta = rec
        elif kind == "turn":
            turns[rec["turn_order"]] = FinalTurn(
                rec["turn_order"],
                rec["speaker"],
                rec["text"],
                datetime.fromisoformat(rec["started_at"]),
                datetime.fromisoformat(rec["ended_at"]),
                language=rec.get("language"),
            )
        elif kind == "translation":
            translations[rec["turn_order"]] = Translation(
                rec["turn_order"], rec["text"], rec.get("error")
            )
        elif kind == "revision":
            revisions[rec["turn_order"]] = rec["speaker"]
    entries = []
    for order in sorted(turns):
        turn = turns[order]
        speaker = revisions.get(order, turn.speaker)
        if speaker != turn.speaker:
            turn = replace(turn, speaker=speaker)
        entries.append(Entry(turn, translations.get(order)))
    return meta, entries


def markdown_block(turn: FinalTurn, tr: Translation | None) -> str:
    stamp = turn.started_at.strftime("%H:%M:%S")
    lines = [f"**{stamp} {turn.speaker}**: {turn.text}"]
    if tr is not None:
        lines.append(tr.text if tr.error is None else f"_(translation failed: {tr.error})_")
    return "\n".join(lines) + "\n\n"
