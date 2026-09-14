"""AssemblyAI streaming wrapper. Emits PartialTurn / FinalTurn / SpeakerRevision."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from assemblyai.streaming.v3 import (
    BeginEvent,
    SpeakerRevisionEvent,
    SpeechModel,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingParameters,
    TerminationEvent,
    TurnEvent,
)

from meeting_assist.audio import AudioSource
from meeting_assist.events import Event, FinalTurn, PartialTurn, SpeakerRevision
from meeting_assist.stt import DEFAULT_LANGUAGES, TranscriberSettings

log = logging.getLogger(__name__)


def build_params(
    sample_rate: int,
    keyterms: list[str],
    prompt: str,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
) -> StreamingParameters:
    """One language code forces a monolingual session. Two or more let the
    model switch between them mid-sentence; language detection is turned on
    so each turn reports which language it ended up in."""
    return StreamingParameters(
        sample_rate=sample_rate,
        speech_model=SpeechModel.universal_3_5_pro,
        speaker_labels=True,
        format_turns=True,
        language_codes=list(languages),
        language_detection=True if len(languages) > 1 else None,
        keyterms_prompt=keyterms or None,
        prompt=prompt or None,
    )


class TurnMapper:
    """Pure translation of AssemblyAI events into our dataclasses.

    With `format_turns` on, the server sends each end-of-turn twice: first
    unformatted, then formatted. The formatted one becomes the FinalTurn.
    The unformatted one is held so that, if its formatted version never
    arrives (seen in a real 29-minute session), the text is not lost: it is
    emitted when a later turn finalises or when `flush_stale()` is called at
    session end.
    """

    def __init__(self, session_start: datetime, order_offset: int = 0) -> None:
        self._start = session_start
        self._offset = order_offset
        self._finalised: set[int] = set()
        self._held: dict[int, FinalTurn] = {}
        self._next_expected = order_offset
        self.last_order = -1
        # map_turn runs on the SDK read thread; flush_stale on the run
        # thread after disconnect(). disconnect() failures are swallowed, so
        # the read thread may still be alive at that point.
        self._lock = threading.Lock()

    def map_turn(self, event: TurnEvent) -> list[PartialTurn | FinalTurn]:
        with self._lock:
            return self._map_turn(event)

    def _map_turn(self, event: TurnEvent) -> list[PartialTurn | FinalTurn]:
        text = (event.transcript or "").strip()
        order = event.turn_order + self._offset
        if not text:
            if event.end_of_turn and order not in self._finalised:
                log.warning("turn %d ended with an empty transcript", order)
                self._finalise(order)
            return []
        self.last_order = max(self.last_order, order)
        speaker = event.speaker_label or "?"
        if not event.end_of_turn:
            return [PartialTurn(order, speaker, text)]
        if order in self._finalised:
            return []
        words = event.words or []
        started = self._start + timedelta(milliseconds=words[0].start) if words else self._start
        ended = self._start + timedelta(milliseconds=words[-1].end) if words else self._start
        final = FinalTurn(order, speaker, text, started, ended, language=event.language_code)
        if not event.turn_is_formatted:
            self._held[order] = final
            return []
        out: list[PartialTurn | FinalTurn] = list(self._flush_stale(before=order))
        self._held.pop(order, None)
        self._finalise(order)
        out.append(final)
        return out

    def flush_stale(self, before: int | None = None) -> list[FinalTurn]:
        """Emit held unformatted turns (all, or those below `before`) whose
        formatted version never came."""
        with self._lock:
            return self._flush_stale(before)

    def _flush_stale(self, before: int | None = None) -> list[FinalTurn]:
        out = []
        for order in sorted(self._held):
            if before is not None and order >= before:
                break
            log.warning(
                "turn %d: formatted transcript never arrived, using unformatted text", order
            )
            out.append(self._held.pop(order))
            self._finalise(order)
        return out

    def _finalise(self, order: int) -> None:
        self._finalised.add(order)
        if order > self._next_expected:
            log.warning("turn orders %d-%d never finalised", self._next_expected, order - 1)
        self._next_expected = max(self._next_expected, order + 1)

    def map_revision(self, event: SpeakerRevisionEvent) -> list[SpeakerRevision]:
        return [
            SpeakerRevision(item.turn_order + self._offset, item.speaker_label or "?")
            for item in event.revisions
        ]


class AssemblyAITranscriber:
    def __init__(
        self,
        settings: TranscriberSettings,
        client_factory: Callable[[], Any] | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._params = build_params(
            settings.sample_rate, settings.keyterms, settings.prompt, settings.languages
        )
        self._factory = client_factory or (
            lambda: StreamingClient(StreamingClientOptions(api_key=settings.api_key))
        )
        self._max_reconnects = settings.max_reconnects
        self._reconnect_delay = settings.reconnect_delay
        self._now = now
        self._on_event: Callable[[Event], None] = lambda e: None
        self._on_status: Callable[[str], None] = lambda s: None
        self._stop = threading.Event()
        self._failed = threading.Event()
        self._mapper = TurnMapper(now())
        self._client: Any | None = None
        self._reconnects = 0
        self._high_order = -1
        self.abort_reason: str | None = None

    # -- event handlers (run on the SDK read thread) -------------------------

    def _on_begin(self, _client: Any, event: BeginEvent) -> None:
        self._high_order = max(self._high_order, self._mapper.last_order)
        self._mapper = TurnMapper(self._now(), order_offset=self._high_order + 1)
        self._reconnects = 0
        self._on_status(f"connected {event.id[:8]}")

    def _on_turn(self, _client: Any, event: TurnEvent) -> None:
        for mapped in self._mapper.map_turn(event):
            self._on_event(mapped)

    def _on_revision(self, _client: Any, event: SpeakerRevisionEvent) -> None:
        for rev in self._mapper.map_revision(event):
            self._on_event(rev)

    def _on_error(self, _client: Any, error: StreamingError) -> None:
        log.error("AssemblyAI error: %s", error)
        self._on_status(f"error: {error}")
        self._failed.set()

    def _on_termination(self, _client: Any, event: TerminationEvent) -> None:
        self._on_status(f"session ended after {event.audio_duration_seconds or 0}s audio")
        if not self._stop.is_set():
            # The server ended the session on its own (max duration, idle
            # timeout, rotation) rather than us asking to stop: treat it like
            # an error and go through the same reconnect budget.
            self._failed.set()

    # -- lifecycle -----------------------------------------------------------

    def _flush_stale(self) -> None:
        """Deliver held unformatted turns once the session's read thread is
        done, i.e. after disconnect() returned, so no formatted version can
        still arrive for them."""
        for final in self._mapper.flush_stale():
            self._on_event(final)

    def _session_closed(self) -> bool:
        """True when the current client's connection is already dead even
        though no Error or Termination event told us so.

        assemblyai 1.5.x can close a session cleanly (WebSocket code 1000)
        without dispatching any event at all; the only signal is that the
        SDK client sets its private `_stop_event`, after which `stream()`
        becomes a silent no-op. There is no public API for this, so this
        reads that private attribute on purpose.
        """
        stop_event = getattr(self._client, "_stop_event", None)
        return bool(stop_event is not None and stop_event.is_set())

    def _connect(self) -> Any:
        client = self._factory()
        client.on(StreamingEvents.Begin, self._on_begin)
        client.on(StreamingEvents.Turn, self._on_turn)
        client.on(StreamingEvents.SpeakerRevision, self._on_revision)
        client.on(StreamingEvents.Error, self._on_error)
        client.on(StreamingEvents.Termination, self._on_termination)
        client.connect(self._params)
        return client

    def run(
        self,
        source: AudioSource,
        on_event: Callable[[Event], None],
        on_status: Callable[[str], None],
    ) -> None:
        self._on_event = on_event
        self._on_status = on_status
        self._client = self._connect()
        try:
            for chunk in source.chunks():
                if self._stop.is_set():
                    break
                if self._failed.is_set() or self._session_closed():
                    if not self._reconnect():
                        break
                assert self._client is not None
                self._client.stream(chunk)
        finally:
            self._shutdown()

    def _reconnect(self) -> bool:
        if self._reconnects >= self._max_reconnects:
            self._on_status("gave up reconnecting")
            self.abort_reason = f"gave up reconnecting after {self._max_reconnects} attempts"
            return False
        self._reconnects += 1
        self._on_status(f"reconnecting ({self._reconnects}/{self._max_reconnects})")
        try:
            assert self._client is not None
            self._client.disconnect(terminate=False)
        except Exception:
            log.debug("disconnect during reconnect failed", exc_info=True)
        self._flush_stale()
        time.sleep(self._reconnect_delay)
        self._failed.clear()
        self._client = self._connect()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.disconnect(terminate=True)
        except Exception:
            log.debug("disconnect failed", exc_info=True)
        self._client = None
        self._flush_stale()
