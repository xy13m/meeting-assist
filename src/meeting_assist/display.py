"""Terminal rendering with rich. Completed turns scroll up; pending ones live at the bottom."""

from __future__ import annotations

import threading
import time
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from meeting_assist.events import FinalTurn, PartialTurn, Translation

PALETTE = ["cyan", "green", "magenta", "yellow", "blue", "red"]
UNKNOWN = {"?", "PENDING"}
INDENT = " " * 10


def render_turn(
    turn: FinalTurn,
    translation: Translation | None,
    style: str,
    *,
    awaiting: bool = True,
) -> Text:
    """`awaiting=False` with no translation renders a turn that will never
    get one (already in the target language) without the pending marker."""
    label = "?" if turn.speaker in UNKNOWN else turn.speaker
    text = Text()
    text.append(turn.started_at.strftime("%H:%M:%S"), style="dim")
    text.append("  ")
    text.append(label, style=f"bold {style}")
    text.append("  ")
    text.append(turn.text, style=style)
    if translation is None and not awaiting:
        return text
    text.append("\n" + INDENT)
    if translation is None:
        text.append("…", style="dim")
    elif translation.error is not None:
        text.append(f"(translation failed: {translation.error})", style="red")
    else:
        text.append(translation.text, style="bold white")
    return text


class Display:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._live = Live(Text(""), console=self._console, refresh_per_second=8, transient=True)
        self._lock = threading.RLock()
        self._styles: dict[str, str] = {}
        self._pending: dict[int, FinalTurn] = {}
        self._partial: PartialTurn | None = None
        self._status: dict[str, Any] = {"connection": "starting", "backlog": 0, "dropped": 0}
        self._started = time.monotonic()

    def speaker_style(self, label: str) -> str:
        with self._lock:
            if label in UNKNOWN:
                return "dim"
            if label not in self._styles:
                self._styles[label] = PALETTE[len(self._styles) % len(PALETTE)]
            return self._styles[label]

    def start(self) -> None:
        self._started = time.monotonic()
        self._live.start()

    def stop(self) -> None:
        with self._lock:
            for order in sorted(self._pending):
                turn = self._pending[order]
                self._live.console.print(render_turn(turn, None, self.speaker_style(turn.speaker)))
            self._pending.clear()
        self._live.stop()

    def show_partial(self, p: PartialTurn) -> None:
        with self._lock:
            self._partial = p
            self._refresh()

    def show_turn(self, t: FinalTurn) -> None:
        with self._lock:
            self._pending[t.turn_order] = t
            if self._partial is not None and self._partial.turn_order == t.turn_order:
                self._partial = None
            self._refresh()

    def show_translation(self, tr: Translation) -> None:
        with self._lock:
            turn = self._pending.pop(tr.turn_order, None)
            if turn is not None:
                self._live.console.print(render_turn(turn, tr, self.speaker_style(turn.speaker)))
            self._refresh()

    def show_untranslated(self, turn_order: int) -> None:
        with self._lock:
            turn = self._pending.pop(turn_order, None)
            if turn is not None:
                style = self.speaker_style(turn.speaker)
                self._live.console.print(render_turn(turn, None, style, awaiting=False))
            self._refresh()

    def set_status(self, **fields: Any) -> None:
        with self._lock:
            self._status.update(fields)
            self._refresh()

    def live_text(self) -> str:
        with self._lock:
            return self._render().plain

    def _render(self) -> Text:
        parts: list[Text] = []
        for order in sorted(self._pending):
            turn = self._pending[order]
            parts.append(render_turn(turn, None, self.speaker_style(turn.speaker)))
        if self._partial is not None:
            label = "?" if self._partial.speaker in UNKNOWN else self._partial.speaker
            parts.append(Text(f"{INDENT}{label}  {self._partial.text}", style="dim"))
        elapsed = int(time.monotonic() - self._started)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        status = (
            f"{hours}:{minutes:02d}:{seconds:02d}  {self._status['connection']}  "
            f"backlog {self._status['backlog']}  dropped {self._status['dropped']}"
        )
        parts.append(Text(status, style="reverse"))
        return Text("\n").join(parts)

    def _refresh(self) -> None:
        self._live.update(Group(self._render()))
