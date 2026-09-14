from pathlib import Path

import pytest

from meeting_assist import transcript
from meeting_assist.events import FinalTurn, SpeakerRevision, Translation
from meeting_assist.language import for_code
from meeting_assist.llm import Message
from meeting_assist.summarizer import EmptyTranscript, build_prompt, summarize
from tests.conftest import T0, FakeLLM


def write_meeting(tmp_path: Path, with_context: bool = True) -> Path:
    d = tmp_path / "20260912-100000"
    d.mkdir()
    records = [
        transcript.meta_record(T0, "zh-TW", ("en",)),
        transcript.turn_record(FinalTurn(0, "A", "Hello there.", T0, T0)),
        transcript.translation_record(Translation(0, "你好。")),
        transcript.turn_record(FinalTurn(1, "D", "Ship it Friday.", T0, T0)),
        transcript.revision_record(SpeakerRevision(1, "B")),
    ]
    (d / "transcript.jsonl").write_text("".join(transcript.encode(r) for r in records))
    if with_context:
        (d / "meeting.md").write_text("# Release sync\nAlice is A.\n")
    return d


def test_build_prompt_lists_turns_with_revised_speakers_and_context():
    _, entries = transcript.assemble(
        [
            transcript.turn_record(FinalTurn(0, "A", "Hello there.", T0, T0)),
            transcript.turn_record(FinalTurn(1, "D", "Ship it Friday.", T0, T0)),
            transcript.revision_record(SpeakerRevision(1, "B")),
        ]
    )
    system, user = build_prompt(entries, "# Release sync\n", for_code("zh-TW"))
    assert "## Summary" in system and "## Action items" in system
    assert "Traditional Chinese (Taiwan)" in system
    assert "## Meeting context\n# Release sync" in user
    assert "[10:00:00] A: Hello there.\n[10:00:00] B: Ship it Friday." in user


def test_build_prompt_without_context():
    _, entries = transcript.assemble([transcript.turn_record(FinalTurn(0, "A", "Hi.", T0, T0))])
    _, user = build_prompt(entries, "", for_code("en"))
    assert "(not provided)" in user


def test_summarize_reads_the_meeting_dir_and_calls_the_llm(tmp_path: Path):
    d = write_meeting(tmp_path)
    llm = FakeLLM(["## Summary\n\nShort.\n\n## Action items\n\n- B: ship Friday\n"])
    out = summarize(d, llm, for_code("zh-TW"))
    assert out.startswith("## Summary")
    system, messages, max_tokens = llm.calls[0]
    assert max_tokens == 4000
    assert messages == [Message("user", messages[0].content)]
    assert "Alice is A." in messages[0].content
    assert "[10:00:00] B: Ship it Friday." in messages[0].content
    assert "你好" not in messages[0].content  # original text only, not translations


def test_summarize_without_context_file(tmp_path: Path):
    d = write_meeting(tmp_path, with_context=False)
    llm = FakeLLM(["## Summary\n"])
    summarize(d, llm, for_code("en"))
    assert "(not provided)" in llm.calls[0][1][0].content


def test_summarize_with_no_turns_raises(tmp_path: Path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "transcript.jsonl").write_text(transcript.encode(transcript.meta_record(T0, "en", ["en"])))
    with pytest.raises(EmptyTranscript):
        summarize(d, FakeLLM(), for_code("en"))
