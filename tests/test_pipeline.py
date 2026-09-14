"""End-to-end wiring of Pipeline/build_pipeline with fakes, no network."""

import io
import json
from io import StringIO
from pathlib import Path

from assemblyai.streaming.v3 import SpeakerRevisionEvent, StreamingEvents
from rich.console import Console

from meeting_assist.audio import FileSource
from meeting_assist.context import MeetingContext
from meeting_assist.display import Display
from meeting_assist.events import FinalTurn, PartialTurn
from meeting_assist.language import GenericTarget, for_code
from meeting_assist.pipeline import Pipeline, build_pipeline
from meeting_assist.store import Store
from meeting_assist.stt.assemblyai import AssemblyAITranscriber
from tests.conftest import T0, FakeLLM, write_wav
from tests.stt.fakes import FakeClient, turn


def quiet_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None)


class TurnFiringClient(FakeClient):
    """Fires one formatted end-of-turn TurnEvent on the first streamed chunk."""

    def __init__(self) -> None:
        super().__init__()
        self._fired = False

    def stream(self, data: bytes) -> None:
        super().stream(data)
        if not self._fired:
            self._fired = True
            self.handlers[StreamingEvents.Turn](
                self,
                turn(
                    turn_order=0,
                    end_of_turn=True,
                    turn_is_formatted=True,
                    speaker_label="A",
                    transcript="Hello there.",
                ),
            )


class UnformattedOnlyClient(FakeClient):
    """Fires an unformatted end-of-turn and never the formatted version,
    then a late SpeakerRevision for it, as seen in a real session."""

    def __init__(self) -> None:
        super().__init__()
        self._fired = False

    def stream(self, data: bytes) -> None:
        super().stream(data)
        if not self._fired:
            self._fired = True
            self.handlers[StreamingEvents.Turn](
                self,
                turn(turn_order=0, end_of_turn=True, speaker_label="A", transcript="hello there"),
            )

    def disconnect(self, terminate: bool = False) -> None:
        super().disconnect(terminate)
        self.handlers[StreamingEvents.SpeakerRevision](
            self,
            SpeakerRevisionEvent(revisions=[{"turn_order": 0, "speaker_label": "B", "words": []}]),
        )


def build(tmp_path: Path, client_cls, llm, context_path=None):
    FakeClient.instances.clear()
    wav = tmp_path / "a.wav"
    write_wav(wav, 16000, 16000)  # one second, several 50 ms chunks
    context = MeetingContext.empty()
    if context_path is not None:
        context = MeetingContext(text=context_path.read_text(), keyterms=[], prompt="Test")
    store = Store(
        tmp_path / "meetings", target="zh-TW", languages=("en",), context_path=context_path
    )
    pipeline = build_pipeline(
        source=FileSource(wav, realtime=False),
        llm=llm,
        context=context,
        keyterms=[],
        store=store,
        target=for_code("zh-TW"),
        languages=("en",),
        api_key="key",
        console=quiet_console(),
        transcriber_factory=lambda s: AssemblyAITranscriber(s, client_factory=client_cls),
    )
    return pipeline


def read_jsonl(meeting_dir: Path) -> list[dict]:
    lines = (meeting_dir / "transcript.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_pipeline_run_transcribes_translates_stores_and_displays(tmp_path: Path):
    context_path = tmp_path / "meeting.md"
    context_path.write_text("# Test meeting\n", encoding="utf-8")
    pipeline = build(tmp_path, TurnFiringClient, FakeLLM(["你好。"]), context_path)

    pipeline.run()

    assert pipeline.transcriber.abort_reason is None
    lines = read_jsonl(pipeline.store.meeting_dir)
    turns = [r for r in lines if r["type"] == "turn"]
    translations = [r for r in lines if r["type"] == "translation"]
    assert lines[0]["type"] == "meta"
    assert turns and turns[0]["turn_order"] == 0
    assert translations == [
        {"type": "translation", "turn_order": 0, "text": "你好。", "error": None}
    ]
    assert lines.index(turns[0]) < lines.index(translations[0])
    md = (pipeline.store.meeting_dir / "transcript.md").read_text(encoding="utf-8")
    assert "Hello there." in md and "你好。" in md
    assert (pipeline.store.meeting_dir / "meeting.md").read_text() == "# Test meeting\n"
    console_out = pipeline.display._console.file.getvalue()
    assert "Hello there." in console_out and "你好。" in console_out
    assert not pipeline.translator.is_alive()
    assert FakeClient.instances[0].disconnects == [True]


def test_pipeline_delivers_held_turn_and_revised_label_to_store(tmp_path: Path):
    pipeline = build(tmp_path, UnformattedOnlyClient, FakeLLM(["你好。"]))
    pipeline.run()
    lines = read_jsonl(pipeline.store.meeting_dir)
    assert [(r["type"], r.get("turn_order")) for r in lines] == [
        ("meta", None),
        ("revision", 0),
        ("turn", 0),
        ("translation", 0),
    ]
    md = (pipeline.store.meeting_dir / "transcript.md").read_text(encoding="utf-8")
    assert md.endswith(" B**: hello there\n你好。\n\n")


class FakeTranslator:
    def __init__(self):
        self.submitted = []

    def submit(self, turn):
        self.submitted.append(turn.turn_order)

    def backlog(self):
        return len(self.submitted)


class FakeSource:
    dropped = 0


def make_pipeline(tmp_path: Path, target):
    store = Store(tmp_path / "out", target=target.code, languages=("en", "zh"), now=lambda: T0)
    buf = StringIO()
    display = Display(Console(file=buf, width=80, force_terminal=False))
    translator = FakeTranslator()
    pipeline = Pipeline(FakeSource(), None, translator, display, store, target)
    return pipeline, translator, store, buf


def test_pipeline_skips_translator_for_target_language_turns(tmp_path: Path):
    """A turn spoken mostly in Chinese is shown and stored in Traditional
    characters without translation; every other turn reaches the translator.
    The recogniser's language label plays no part in that decision."""
    pipeline, translator, store, buf = make_pipeline(tmp_path, for_code("zh-TW"))
    pipeline.on_event(FinalTurn(0, "A", "Hello.", T0, T0, language="en"))
    # Simplified from the recogniser, mislabelled "en": still Chinese, still normalised.
    pipeline.on_event(FinalTurn(1, "B", "对，而且 license 只有一个 slot。", T0, T0, language="en"))
    # Plain English mislabelled "zh": must be translated.
    pipeline.on_event(FinalTurn(2, "A", "Bye.", T0, T0, language="zh"))

    assert translator.submitted == [0, 2]
    assert "對，而且 license 只有一個 slot。" in buf.getvalue()
    assert "Hello." not in buf.getvalue().replace("…", "")  # still waiting for translation
    md = store.meeting_dir / "transcript.md"
    assert md.read_text() == "**10:00:00 B**: 對，而且 license 只有一個 slot。\n\n"
    store.close()


def test_pipeline_normalises_partial_turns_too(tmp_path: Path):
    pipeline, _, store, _ = make_pipeline(tmp_path, for_code("zh-TW"))
    pipeline.on_event(PartialTurn(0, "A", "只有一个"))
    assert "只有一個" in pipeline.display.live_text()
    store.close()


def test_pipeline_with_a_generic_target_translates_everything(tmp_path: Path):
    pipeline, translator, store, _ = make_pipeline(tmp_path, GenericTarget("en"))
    pipeline.on_event(FinalTurn(0, "A", "对，只有一个 slot。", T0, T0))
    assert translator.submitted == [0]
    lines = (store.meeting_dir / "transcript.jsonl").read_text()
    assert "对，只有一个 slot。" in lines  # no Simplified-to-Traditional conversion
    store.close()
