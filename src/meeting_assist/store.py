"""Transcript files for one meeting.

`transcript.jsonl` is append-only and flushed per record. `transcript.md` is
appended live as translations arrive, then rewritten once on close so that
speaker revisions (which the recogniser sends at session end) are applied.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from meeting_assist import transcript
from meeting_assist.events import FinalTurn, SpeakerRevision, Translation


class Store:
    def __init__(
        self,
        root: Path,
        *,
        target: str,
        languages: Sequence[str],
        context_path: Path | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        started = now()
        self.meeting_dir = Path(root) / started.strftime("%Y%m%d-%H%M%S")
        self.meeting_dir.mkdir(parents=True, exist_ok=True)
        if context_path is not None:
            shutil.copyfile(context_path, self.meeting_dir / "meeting.md")
        self._jsonl = (self.meeting_dir / "transcript.jsonl").open("a", encoding="utf-8")
        self._md_path = self.meeting_dir / "transcript.md"
        self._md = self._md_path.open("a", encoding="utf-8")
        self._pending: dict[int, FinalTurn] = {}
        self._turns: dict[int, FinalTurn] = {}
        self._translations: dict[int, Translation] = {}
        self._revisions: dict[int, str] = {}
        self._lock = threading.Lock()
        self._record(transcript.meta_record(started, target, languages))

    def write_turn(self, turn: FinalTurn) -> None:
        with self._lock:
            self._pending[turn.turn_order] = turn
            self._turns[turn.turn_order] = turn
            self._record(transcript.turn_record(turn))

    def write_translation(self, tr: Translation) -> None:
        with self._lock:
            self._record(transcript.translation_record(tr))
            self._translations[tr.turn_order] = tr
            self._append_block(tr.turn_order, tr)

    def mark_untranslated(self, turn_order: int) -> None:
        """The turn needs no translation (it was already in the target
        language): write its Markdown block now instead of waiting."""
        with self._lock:
            self._append_block(turn_order, None)

    def write_revision(self, rev: SpeakerRevision) -> None:
        with self._lock:
            self._record(transcript.revision_record(rev))
            self._revisions[rev.turn_order] = rev.speaker

    def close(self) -> None:
        """Flush untranslated turns, then rewrite transcript.md with revised
        speaker labels. The rewrite goes through a temp file so a crash
        mid-write leaves the live version intact."""
        with self._lock:
            for order in sorted(self._pending):
                self._md.write(transcript.markdown_block(self._pending[order], None))
            self._pending.clear()
            self._md.close()
            self._jsonl.close()
            self._rewrite_markdown()

    def _append_block(self, turn_order: int, tr: Translation | None) -> None:
        turn = self._pending.pop(turn_order, None)
        if turn is not None:
            self._md.write(transcript.markdown_block(turn, tr))
            self._md.flush()

    def _rewrite_markdown(self) -> None:
        records = [transcript.turn_record(t) for t in self._turns.values()]
        records += [transcript.translation_record(t) for t in self._translations.values()]
        records += [
            transcript.revision_record(SpeakerRevision(order, speaker))
            for order, speaker in self._revisions.items()
        ]
        _, entries = transcript.assemble(records)
        tmp = self._md_path.with_suffix(".md.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write("".join(transcript.markdown_block(e.turn, e.translation) for e in entries))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._md_path)

    def _record(self, obj: dict[str, Any]) -> None:
        self._jsonl.write(transcript.encode(obj))
        self._jsonl.flush()
