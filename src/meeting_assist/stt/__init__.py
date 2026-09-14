"""Speech-to-text boundary.

The pipeline only sees this protocol and the events in `events.py`. The
AssemblyAI SDK is imported by `stt/assemblyai.py` and nowhere else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from meeting_assist.audio import AudioSource
from meeting_assist.events import Event

DEFAULT_LANGUAGES: tuple[str, ...] = ("en",)


@dataclass(frozen=True)
class TranscriberSettings:
    api_key: str
    sample_rate: int
    keyterms: list[str]
    prompt: str
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    max_reconnects: int = 5
    reconnect_delay: float = 1.0


class Transcriber(Protocol):
    abort_reason: str | None

    def run(
        self,
        source: AudioSource,
        on_event: Callable[[Event], None],
        on_status: Callable[[str], None],
    ) -> None:
        """Stream `source` until it ends or stop() is called. Blocking."""
        ...

    def stop(self) -> None: ...


def create_transcriber(
    settings: TranscriberSettings, client_factory: Callable[[], Any] | None = None
) -> Transcriber:
    from meeting_assist.stt.assemblyai import AssemblyAITranscriber

    return AssemblyAITranscriber(settings, client_factory=client_factory)
