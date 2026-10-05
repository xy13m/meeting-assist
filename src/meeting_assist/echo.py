"""Drops microphone turns that are only the meeting audio heard through
the speakers.

The check is on text: a microphone turn whose words mostly appear in a
system-audio turn from the same moment is an echo. Pass-through unless
enabled (`listen --speakers`).
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta

from meeting_assist.events import Event, FinalTurn, PartialTurn

log = logging.getLogger(__name__)

_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_TOKEN = re.compile(rf"[{_CJK}]|[^\W_{_CJK}]+")


def tokens(text: str) -> Counter[str]:
    """Words in Latin script, lowercased; each CJK character on its own."""
    return Counter(m.group(0).lower() for m in _TOKEN.finditer(text))


def echo_score(text: str, candidates: list[str]) -> float:
    """Share of the tokens in `text` that also occur in `candidates`."""
    mine = tokens(text)
    total = sum(mine.values())
    if total == 0:
        return 0.0
    pool: Counter[str] = Counter()
    for candidate in candidates:
        pool.update(tokens(candidate))
    return sum(min(n, pool[t]) for t, n in mine.items()) / total


class EchoFilter:
    def __init__(
        self,
        forward: Callable[[Event], None],
        *,
        enabled: bool,
        threshold: float = 0.6,
        hold: float = 3.0,
        window: float = 2.0,
        tick: float = 0.5,
        memory: float = 60.0,
        clock: Callable[[], datetime] = datetime.now,
        on_drop: Callable[[int], None] = lambda dropped: None,
    ) -> None:
        self._forward = forward
        self.enabled = enabled
        self._threshold = threshold
        self._hold = timedelta(seconds=hold)
        self._window = timedelta(seconds=window)
        self._tick = tick
        self._memory = timedelta(seconds=memory)
        self._clock = clock
        self._on_drop = on_drop
        self._finals: list[FinalTurn] = []
        self._partials: dict[int, PartialTurn] = {}
        self._held: list[FinalTurn] = []
        self.dropped = 0
        # on_system, on_mic, and the ticker run on three threads. Events are
        # forwarded under the lock so they leave in the order decided here.
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None

    def start(self) -> None:
        if self.enabled and self._ticker is None:
            self._ticker = threading.Thread(target=self._tick_loop, name="echo-filter", daemon=True)
            self._ticker.start()

    def on_system(self, event: Event) -> None:
        with self._lock:
            self._forward(event)
            if not self.enabled:
                return
            if isinstance(event, PartialTurn):
                self._partials[event.turn_order] = event
            elif isinstance(event, FinalTurn):
                # A partial at or below this order will not get a final turn
                # any more; keeping it would match unrelated echoes later.
                self._partials = {o: p for o, p in self._partials.items() if o > event.turn_order}
                self._finals.append(event)
            self._settle(final=False)

    def on_mic(self, event: Event) -> None:
        with self._lock:
            if not self.enabled:
                self._forward(event)
            elif isinstance(event, FinalTurn):
                self._held.append(event)
                self._settle(final=False)
            elif not isinstance(event, PartialTurn):
                self._forward(event)

    def check(self) -> None:
        with self._lock:
            self._settle(final=False)

    def close(self) -> None:
        """Stop the ticker and decide every held turn now."""
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=2.0)
        with self._lock:
            self._settle(final=True)

    def _tick_loop(self) -> None:
        while not self._stop.wait(self._tick):
            self.check()

    def _settle(self, *, final: bool) -> None:
        now = self._clock()
        self._finals = [f for f in self._finals if f.ended_at >= now - self._memory]
        still_held = []
        for turn in self._held:
            matches = self._candidates(turn)
            score = echo_score(turn.text, [m.text for m in matches])
            if score >= self._threshold:
                self.dropped += 1
                log.warning(
                    "dropped microphone turn as echo (score %.2f, system turns %s): %s",
                    score,
                    [m.turn_order for m in matches],
                    turn.text,
                )
                self._on_drop(self.dropped)
            elif final or now - turn.ended_at >= self._hold:
                self._forward(turn)
            else:
                still_held.append(turn)
        self._held = still_held

    def _candidates(self, turn: FinalTurn) -> list[FinalTurn | PartialTurn]:
        near: list[FinalTurn | PartialTurn] = [
            f
            for f in self._finals
            if f.started_at - self._window <= turn.ended_at
            and f.ended_at + self._window >= turn.started_at
        ]
        return near + list(self._partials.values())
