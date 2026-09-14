"""Shared test helpers. FakeLLM stands in for any LLM provider."""

from __future__ import annotations

import struct
import wave
from datetime import datetime
from pathlib import Path

T0 = datetime(2026, 9, 12, 10, 0, 0)


class FakeLLM:
    """Pops canned responses in order; an Exception item is raised instead of returned."""

    model = "fake-model"

    def __init__(self, responses: list[str | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, list, int]] = []

    def complete(self, system: str, messages: list, max_tokens: int) -> str:
        self.calls.append((system, list(messages), max_tokens))
        if not self.responses:
            raise AssertionError("FakeLLM ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def write_wav(path: Path, rate: int, samples: int) -> None:
    """Write a mono 16-bit WAV of constant amplitude."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{samples}h", *([1000] * samples)))
