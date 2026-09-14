import json
from pathlib import Path

from meeting_assist.events import FinalTurn, SpeakerRevision, Translation
from meeting_assist.store import Store
from tests.conftest import T0


def make_turn(order, speaker="A", text="Hello there."):
    return FinalTurn(order, speaker, text, T0, T0)


def make_store(root: Path, **kw) -> Store:
    kw.setdefault("target", "zh-TW")
    kw.setdefault("languages", ("en",))
    return Store(root, now=lambda: T0, **kw)


def records(store: Store) -> list[dict]:
    lines = (store.meeting_dir / "transcript.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_creates_timestamped_dir_and_copies_context(tmp_path: Path):
    ctx = tmp_path / "notes.md"
    ctx.write_text("# Weekly sync\n")
    store = make_store(tmp_path / "meetings", context_path=ctx)
    assert store.meeting_dir == tmp_path / "meetings" / "20260912-100000"
    assert (store.meeting_dir / "meeting.md").read_text() == "# Weekly sync\n"
    store.close()


def test_without_context_writes_no_meeting_md(tmp_path: Path):
    store = make_store(tmp_path)
    assert not (store.meeting_dir / "meeting.md").exists()
    store.close()


def test_first_record_is_meta(tmp_path: Path):
    store = make_store(tmp_path, target="ja", languages=("en", "ja"))
    store.close()
    assert records(store) == [
        {
            "type": "meta",
            "version": 1,
            "started_at": "2026-09-12T10:00:00",
            "target": "ja",
            "languages": ["en", "ja"],
        }
    ]


def test_jsonl_records_every_event_immediately(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(make_turn(0))
    store.write_translation(Translation(0, "你好。"))
    store.write_revision(SpeakerRevision(0, "B"))
    lines = records(store)[1:]
    assert [line["type"] for line in lines] == ["turn", "translation", "revision"]
    assert lines[0] == {
        "type": "turn",
        "turn_order": 0,
        "speaker": "A",
        "text": "Hello there.",
        "started_at": "2026-09-12T10:00:00",
        "ended_at": "2026-09-12T10:00:00",
        "language": None,
    }
    assert lines[1] == {"type": "translation", "turn_order": 0, "text": "你好。", "error": None}
    assert lines[2] == {"type": "revision", "turn_order": 0, "speaker": "B"}
    store.close()


def test_markdown_pairs_turn_with_translation(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(make_turn(0, "A", "Hello there."))
    store.write_turn(make_turn(1, "B", "Hi."))
    md = store.meeting_dir / "transcript.md"
    assert md.read_text() == ""  # nothing until a translation arrives
    store.write_translation(Translation(0, "你好。"))
    assert md.read_text() == "**10:00:00 A**: Hello there.\n你好。\n\n"
    store.write_translation(Translation(1, "", error="boom"))
    assert md.read_text().endswith("**10:00:00 B**: Hi.\n_(translation failed: boom)_\n\n")
    store.close()


def test_mark_untranslated_writes_block_without_translation_line(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(FinalTurn(0, "A", "你好。", T0, T0, language="zh"))
    md = store.meeting_dir / "transcript.md"
    assert md.read_text() == ""
    store.mark_untranslated(0)
    assert md.read_text() == "**10:00:00 A**: 你好。\n\n"
    store.mark_untranslated(0)  # idempotent
    store.write_revision(SpeakerRevision(0, "B"))
    store.close()
    assert md.read_text() == "**10:00:00 B**: 你好。\n\n"
    assert records(store)[1]["language"] == "zh"


def test_close_flushes_untranslated_turns(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(make_turn(5, "C", "Bye."))
    store.close()
    assert (store.meeting_dir / "transcript.md").read_text() == "**10:00:00 C**: Bye.\n\n"


def test_close_rewrites_markdown_with_revised_speakers(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(make_turn(0, "A", "Hello there."))
    store.write_translation(Translation(0, "你好。"))
    store.write_turn(make_turn(1, "D", "Hi."))
    store.write_translation(Translation(1, "嗨。"))
    store.write_turn(make_turn(2, "A", "Bye."))
    # Revisions arrive at session end, after the live blocks were written.
    store.write_revision(SpeakerRevision(1, "B"))
    store.write_revision(SpeakerRevision(2, "C"))
    store.close()
    md = (store.meeting_dir / "transcript.md").read_text()
    assert md == (
        "**10:00:00 A**: Hello there.\n你好。\n\n"
        "**10:00:00 B**: Hi.\n嗨。\n\n"
        "**10:00:00 C**: Bye.\n\n"
    )
    assert not (store.meeting_dir / "transcript.md.tmp").exists()


def test_revision_for_unknown_turn_is_recorded_but_ignored_in_markdown(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(make_turn(0, "A", "Hello there."))
    store.write_revision(SpeakerRevision(45, "B"))
    store.close()
    assert (store.meeting_dir / "transcript.md").read_text() == "**10:00:00 A**: Hello there.\n\n"
    assert records(store)[-1] == {"type": "revision", "turn_order": 45, "speaker": "B"}


def test_close_preserves_failed_translation_marker_when_rewriting(tmp_path: Path):
    store = make_store(tmp_path)
    store.write_turn(make_turn(0, "A", "Hello there."))
    store.write_translation(Translation(0, "", error="boom"))
    store.write_revision(SpeakerRevision(0, "B"))
    store.close()
    md = (store.meeting_dir / "transcript.md").read_text()
    assert md == "**10:00:00 B**: Hello there.\n_(translation failed: boom)_\n\n"
