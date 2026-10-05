"""Routes events between the stages of one `listen` run.

audio -> transcriber -+
mic   -> transcriber -+-> echo filter -> (display, store, translator) -> (display, store).

The microphone session is optional (`listen --mic`) and runs on its own
thread. System audio is the primary source: when it stops, the run stops.
The pipeline depends only on protocols; it never sees a vendor SDK and
never makes a language-specific decision itself.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable, Sequence

from rich.console import Console

from meeting_assist.audio import AudioSource
from meeting_assist.context import MeetingContext
from meeting_assist.display import Display
from meeting_assist.echo import EchoFilter
from meeting_assist.events import (
    ME_SPEAKER,
    MIC_ORDER_BASE,
    Event,
    FinalTurn,
    PartialTurn,
    SpeakerRevision,
    Translation,
)
from meeting_assist.language import TargetLanguage
from meeting_assist.llm import LLM
from meeting_assist.store import Store
from meeting_assist.stt import Transcriber, TranscriberSettings, create_transcriber
from meeting_assist.translator import Translator

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        source: AudioSource,
        transcriber: Transcriber,
        translator: Translator,
        display: Display,
        store: Store,
        target: TargetLanguage,
        *,
        mic_source: AudioSource | None = None,
        mic_transcriber: Transcriber | None = None,
        speakers: bool = False,
    ) -> None:
        self.source = source
        self.transcriber = transcriber
        self.translator = translator
        self.display = display
        self.store = store
        self.target = target
        self.mic_source = mic_source
        self.mic_transcriber = mic_transcriber
        self.mic_abort_reason: str | None = None
        self.echo = EchoFilter(
            self.on_event,
            enabled=speakers,
            on_drop=lambda dropped: self.display.set_status(echo=dropped),
        )

    def _dropped(self) -> int:
        return self.source.dropped + (self.mic_source.dropped if self.mic_source else 0)

    def on_event(self, event: Event) -> None:
        if isinstance(event, PartialTurn):
            event = dataclasses.replace(event, text=self.target.normalize(event.text))
            self.display.show_partial(event)
        elif isinstance(event, FinalTurn):
            event = dataclasses.replace(event, text=self.target.normalize(event.text))
            self.store.write_turn(event)
            self.display.show_turn(event)
            if self.target.is_already_target(event.text):
                self.store.mark_untranslated(event.turn_order)
                self.display.show_untranslated(event.turn_order)
            else:
                self.translator.submit(event)
            self.display.set_status(backlog=self.translator.backlog(), dropped=self._dropped())
        elif isinstance(event, SpeakerRevision):
            self.store.write_revision(event)

    def on_translation(self, tr: Translation) -> None:
        self.store.write_translation(tr)
        self.display.show_translation(tr)
        self.display.set_status(backlog=self.translator.backlog())

    def on_status(self, text: str) -> None:
        self.display.set_status(connection=text)

    def on_mic_status(self, text: str) -> None:
        self.display.set_status(mic=text)

    def abort_reasons(self) -> list[str]:
        reasons = []
        if self.transcriber.abort_reason:
            reasons.append(f"system audio: {self.transcriber.abort_reason}")
        mic = self.mic_abort_reason or (
            self.mic_transcriber.abort_reason if self.mic_transcriber else None
        )
        if mic:
            reasons.append(f"microphone: {mic}")
        return reasons

    def _run_mic(self) -> None:
        """Microphone thread. A failure here is shown and logged, and the run
        goes on without the microphone."""
        assert self.mic_transcriber is not None and self.mic_source is not None
        try:
            self.mic_transcriber.run(self.mic_source, self.echo.on_mic, self.on_mic_status)
        except Exception as exc:
            log.exception("microphone transcriber failed")
            self.mic_abort_reason = str(exc) or type(exc).__name__
        reason = self.mic_abort_reason or self.mic_transcriber.abort_reason
        if reason:
            self.display.set_status(mic=f"stopped: {reason}")

    def run(self) -> None:
        self.display.start()
        self.translator.start(self.on_translation)
        self.echo.start()
        if self.echo.enabled:
            self.display.set_status(echo=0)
        mic_thread: threading.Thread | None = None
        if self.mic_transcriber is not None:
            self.display.set_status(mic="starting")
            mic_thread = threading.Thread(target=self._run_mic, name="mic-transcriber", daemon=True)
            mic_thread.start()
        try:
            self.transcriber.run(self.source, self.echo.on_system, self.on_status)
        except KeyboardInterrupt:
            self.transcriber.stop()
        finally:
            if mic_thread is not None and self.mic_transcriber is not None:
                self.mic_transcriber.stop()
                mic_thread.join(timeout=10.0)
            self.echo.close()
            self.source.close()
            if self.mic_source is not None:
                self.mic_source.close()
            self.translator.stop(timeout=10.0)
            self.display.stop()
            self.store.close()


def build_pipeline(
    *,
    source: AudioSource,
    llm: LLM,
    context: MeetingContext,
    keyterms: list[str],
    store: Store,
    target: TargetLanguage,
    languages: Sequence[str],
    api_key: str,
    console: Console | None = None,
    transcriber_factory: Callable[[TranscriberSettings], Transcriber] = create_transcriber,
    mic_source: AudioSource | None = None,
    speakers: bool = False,
) -> Pipeline:
    """Wire the stages for one run. `console` and `transcriber_factory` are
    injection seams for tests."""
    settings = TranscriberSettings(
        api_key=api_key,
        sample_rate=source.sample_rate,
        keyterms=keyterms,
        prompt=context.prompt,
        languages=tuple(languages),
    )
    transcriber = transcriber_factory(settings)
    mic_transcriber = None
    if mic_source is not None:
        mic_transcriber = transcriber_factory(
            dataclasses.replace(
                settings,
                sample_rate=mic_source.sample_rate,
                speaker=ME_SPEAKER,
                order_base=MIC_ORDER_BASE,
            )
        )
    return Pipeline(
        source=source,
        transcriber=transcriber,
        translator=Translator(llm, context.text, target),
        display=Display(console),
        store=store,
        target=target,
        mic_source=mic_source,
        mic_transcriber=mic_transcriber,
        speakers=speakers,
    )
