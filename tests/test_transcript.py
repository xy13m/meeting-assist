import json
from pathlib import Path

from meeting_assist.events import FinalTurn, SpeakerRevision, Translation
from meeting_assist.transcript import (
    Entry,
    assemble,
    encode,
    markdown_block,
    meta_record,
    read_records,
    revision_record,
    translation_record,
    turn_record,
)
from tests.conftest import T0


def make_turn(order, speaker="A", text="Hello there."):
    return FinalTurn(order, speaker, text, T0, T0)


def test_encode_uses_json_default_separators_and_keeps_unicode():
    line = encode({"type": "turn", "text": "你好"})
    assert line == '{"type": "turn", "text": "你好"}\n'


def test_record_shapes():
    assert meta_record(T0, "zh-TW", ("en", "zh")) == {
        "type": "meta",
        "version": 1,
        "started_at": "2026-09-12T10:00:00",
        "target": "zh-TW",
        "languages": ["en", "zh"],
    }
    assert turn_record(FinalTurn(0, "A", "Hi.", T0, T0, language="en")) == {
        "type": "turn",
        "turn_order": 0,
        "speaker": "A",
        "text": "Hi.",
        "started_at": "2026-09-12T10:00:00",
        "ended_at": "2026-09-12T10:00:00",
        "language": "en",
    }
    assert translation_record(Translation(0, "嗨。")) == {
        "type": "translation",
        "turn_order": 0,
        "text": "嗨。",
        "error": None,
    }
    assert revision_record(SpeakerRevision(0, "B")) == {
        "type": "revision",
        "turn_order": 0,
        "speaker": "B",
    }


def test_read_records_skips_blank_lines(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type": "meta"}\n\n{"type": "turn"}\n')
    assert read_records(p) == [{"type": "meta"}, {"type": "turn"}]


def test_assemble_applies_revisions_and_pairs_translations(tmp_path: Path):
    records = [
        meta_record(T0, "zh-TW", ("en",)),
        turn_record(make_turn(1, "D", "Hi.")),
        turn_record(make_turn(0, "A", "Hello there.")),
        translation_record(Translation(0, "你好。")),
        revision_record(SpeakerRevision(1, "B")),
        revision_record(SpeakerRevision(45, "C")),  # unknown turn: ignored
        translation_record(Translation(1, "", error="boom")),
    ]
    meta, entries = assemble(records)
    assert meta is not None and meta["target"] == "zh-TW"
    assert entries == [
        Entry(make_turn(0, "A", "Hello there."), Translation(0, "你好。")),
        Entry(make_turn(1, "B", "Hi."), Translation(1, "", error="boom")),
    ]


def test_assemble_without_meta_or_translations():
    meta, entries = assemble([turn_record(make_turn(0))])
    assert meta is None
    assert entries == [Entry(make_turn(0), None)]


def test_markdown_block_shapes():
    turn = make_turn(0, "A", "Hello there.")
    assert markdown_block(turn, Translation(0, "你好。")) == (
        "**10:00:00 A**: Hello there.\n你好。\n\n"
    )
    assert markdown_block(turn, Translation(0, "", error="boom")) == (
        "**10:00:00 A**: Hello there.\n_(translation failed: boom)_\n\n"
    )
    assert markdown_block(turn, None) == "**10:00:00 A**: Hello there.\n\n"


def test_records_round_trip_through_json():
    rec = turn_record(make_turn(0, "A", "你好"))
    assert json.loads(encode(rec)) == rec
