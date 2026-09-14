"""Routes events between the stages of one `listen` run.

audio -> transcriber -> (display, store, translator) -> (display, store).
The pipeline depends only on protocols; it never sees a vendor SDK and
never makes a language-specific decision itself.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence

from rich.console import Console

from meeting_assist.audio import AudioSource
from meeting_assist.context import MeetingContext
from meeting_assist.display import Display
from meeting_assist.events import Event, FinalTurn, PartialTurn, SpeakerRevision, Translation
from meeting_assist.language import TargetLanguage
from meeting_assist.llm import LLM
from meeting_assist.store import Store
from meeting_assist.stt import Transcriber, TranscriberSettings, create_transcriber
from meeting_assist.translator import Translator


class Pipeline:
    def __init__(
        self,
        source: AudioSource,
        transcriber: Transcriber,
        translator: Translator,
        display: Display,
        store: Store,
        target: TargetLanguage,
    ) -> None:
        self.source = source
        self.transcriber = transcriber
        self.translator = translator
        self.display = display
        self.store = store
        self.target = target

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
            self.display.set_status(backlog=self.translator.backlog(), dropped=self.source.dropped)
        elif isinstance(event, SpeakerRevision):
            self.store.write_revision(event)

    def on_translation(self, tr: Translation) -> None:
        self.store.write_translation(tr)
        self.display.show_translation(tr)
        self.display.set_status(backlog=self.translator.backlog())

    def on_status(self, text: str) -> None:
        self.display.set_status(connection=text)

    def run(self) -> None:
        self.display.start()
        self.translator.start(self.on_translation)
        try:
            self.transcriber.run(self.source, self.on_event, self.on_status)
        except KeyboardInterrupt:
            self.transcriber.stop()
        finally:
            self.source.close()
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
    return Pipeline(
        source=source,
        transcriber=transcriber_factory(settings),
        translator=Translator(llm, context.text, target),
        display=Display(console),
        store=store,
        target=target,
    )
