"""Ordered, single-worker translation of FinalTurns through the LLM interface.

One worker on purpose: output order must match speech order. Do not
parallelise without adding reordering.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable

from meeting_assist.events import FinalTurn, Translation
from meeting_assist.language import TargetLanguage
from meeting_assist.llm import LLM, Message

log = logging.getLogger(__name__)

SYSTEM_INSTRUCTIONS = (
    "You are a live interpreter for a business meeting. Each user message is one "
    "utterance from a participant, transcribed by speech recognition. Translate it "
    "into {target}.\n"
    "Rules:\n"
    "- Output only the translation, nothing else.\n"
    "- Keep people's names, company names, product names, and acronyms as written.\n"
    "- Words already in {target} stay as they are.\n"
    "- Match the length and register of the original; do not expand or explain.\n"
    "- The transcript may contain recognition errors; use the meeting context to "
    "resolve them silently.\n"
    "- Earlier messages in the conversation are previous utterances and your "
    "translations; keep terminology consistent with them."
)


class Translator:
    def __init__(
        self,
        llm: LLM,
        context_text: str,
        target: TargetLanguage,
        *,
        history_size: int = 10,
        retry_delay: float = 1.0,
    ) -> None:
        self._llm = llm
        self._context = context_text
        self._target = target
        self._retry_delay = retry_delay
        self._history: deque[tuple[str, str]] = deque(maxlen=history_size)
        self._queue: queue.Queue[FinalTurn | None] = queue.Queue()
        self._on_translation: Callable[[Translation], None] = lambda tr: None
        self._thread = threading.Thread(target=self._worker, name="translator", daemon=True)

    # -- prompt assembly -----------------------------------------------------

    def system_prompt(self) -> str:
        context = self._context.strip() or "(not provided)"
        instructions = SYSTEM_INSTRUCTIONS.format(target=self._target.name)
        return f"{instructions}\n\n## Meeting context\n{context}"

    def build_messages(self, turn: FinalTurn) -> list[Message]:
        messages: list[Message] = []
        for original, translated in self._history:
            messages.append(Message("user", original))
            messages.append(Message("assistant", translated))
        messages.append(Message("user", turn.text))
        return messages

    def translate_once(self, turn: FinalTurn) -> str:
        return self._llm.complete(self.system_prompt(), self.build_messages(turn), max_tokens=400)

    # -- worker --------------------------------------------------------------

    def start(self, on_translation: Callable[[Translation], None]) -> None:
        self._on_translation = on_translation
        self._thread.start()

    def submit(self, turn: FinalTurn) -> None:
        self._queue.put(turn)

    def backlog(self) -> int:
        return self._queue.qsize()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self, timeout: float = 10.0) -> None:
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _worker(self) -> None:
        while True:
            turn = self._queue.get()
            if turn is None:
                return
            try:
                self._on_translation(self._translate_with_retry(turn))
            except Exception:
                log.exception("on_translation callback failed for turn %d", turn.turn_order)

    def _translate_with_retry(self, turn: FinalTurn) -> Translation:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                text = self.translate_once(turn)
                self._history.append((turn.text, text))
                return Translation(turn.turn_order, text)
            except Exception as exc:
                last_error = exc
                log.warning("translation attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    time.sleep(self._retry_delay)
        return Translation(turn.turn_order, "", error=str(last_error))
