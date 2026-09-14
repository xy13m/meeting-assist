"""Fakes for the AssemblyAI streaming client. Only tests under tests/stt/
may import the SDK's types; everything else works with our own events."""

from __future__ import annotations

import threading
from typing import Any

from assemblyai.streaming.v3 import BeginEvent, StreamingEvents, TurnEvent


def turn(**kw: Any) -> TurnEvent:
    base: dict[str, Any] = {
        "type": "Turn",
        "turn_order": 0,
        "turn_is_formatted": False,
        "end_of_turn": False,
        "transcript": "hello there",
        "end_of_turn_confidence": 0.1,
        "speaker_label": "A",
        "words": [
            {"start": 1000, "end": 1400, "confidence": 0.9, "text": "hello", "word_is_final": True},
            {"start": 1500, "end": 2000, "confidence": 0.9, "text": "there", "word_is_final": True},
        ],
    }
    base.update(kw)
    return TurnEvent(**base)


class FakeClient:
    """Records handlers and streamed chunks; fires Begin on connect."""

    instances: list[FakeClient] = []

    def __init__(self) -> None:
        self.handlers: dict[Any, Any] = {}
        self.chunks: list[bytes] = []
        self.disconnects: list[bool] = []
        FakeClient.instances.append(self)

    def on(self, event: Any, handler: Any) -> None:
        self.handlers[event] = handler

    def connect(self, params: Any) -> None:
        self.handlers[StreamingEvents.Begin](
            self, BeginEvent(type="Begin", id="abcdef1234", expires_at=0)
        )

    def stream(self, data: bytes) -> None:
        self.chunks.append(data)

    def disconnect(self, terminate: bool = False) -> None:
        self.disconnects.append(terminate)


class StopEventClient(FakeClient):
    """Like the real SDK, exposes a private _stop_event set on a silent close."""

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = threading.Event()


class ListSource:
    sample_rate = 16000
    dropped = 0

    def __init__(self, n: int) -> None:
        self._n = n

    def chunks(self):
        for i in range(self._n):
            yield bytes([i])

    def close(self) -> None:
        pass
