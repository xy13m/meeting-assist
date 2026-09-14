from datetime import timedelta

from assemblyai.streaming.v3 import SpeakerRevisionEvent, StreamingParameters

from meeting_assist.events import FinalTurn, PartialTurn, SpeakerRevision
from meeting_assist.stt.assemblyai import TurnMapper, build_params
from tests.conftest import T0
from tests.stt.fakes import turn


def test_partial_turn():
    m = TurnMapper(T0)
    assert m.map_turn(turn()) == [PartialTurn(0, "A", "hello there")]


def test_unformatted_end_of_turn_is_held_not_emitted():
    m = TurnMapper(T0)
    assert m.map_turn(turn(end_of_turn=True)) == []


def test_formatted_end_of_turn_becomes_final_with_timestamps():
    m = TurnMapper(T0)
    out = m.map_turn(turn(end_of_turn=True, turn_is_formatted=True, transcript="Hello there."))
    assert out == [
        FinalTurn(0, "A", "Hello there.", T0 + timedelta(seconds=1), T0 + timedelta(seconds=2))
    ]
    assert m.last_order == 0


def test_formatted_replaces_held_unformatted_turn():
    m = TurnMapper(T0)
    assert m.map_turn(turn(end_of_turn=True, transcript="hello there")) == []
    out = m.map_turn(turn(end_of_turn=True, turn_is_formatted=True, transcript="Hello there."))
    assert [e.text for e in out] == ["Hello there."]
    assert m.flush_stale() == []


def test_held_unformatted_turn_is_emitted_when_a_later_turn_finalises(caplog):
    m = TurnMapper(T0)
    m.map_turn(turn(turn_order=0, end_of_turn=True, transcript="uh huh"))
    with caplog.at_level("WARNING"):
        out = m.map_turn(
            turn(turn_order=1, end_of_turn=True, turn_is_formatted=True, transcript="Right.")
        )
    assert [(e.turn_order, e.text) for e in out] == [(0, "uh huh"), (1, "Right.")]
    assert "turn 0" in caplog.text and "unformatted" in caplog.text
    # The late formatted version must not produce a second FinalTurn.
    late = turn(turn_order=0, end_of_turn=True, turn_is_formatted=True, transcript="Uh-huh.")
    assert m.map_turn(late) == []


def test_flush_stale_emits_all_held_turns_in_order():
    m = TurnMapper(T0)
    m.map_turn(turn(turn_order=3, end_of_turn=True, transcript="three"))
    m.map_turn(turn(turn_order=2, end_of_turn=True, transcript="two"))
    assert [e.turn_order for e in m.flush_stale()] == [2, 3]
    assert m.flush_stale() == []


def test_final_turn_emitted_once_per_order():
    m = TurnMapper(T0)
    e = turn(end_of_turn=True, turn_is_formatted=True)
    assert isinstance(m.map_turn(e)[0], FinalTurn)
    assert m.map_turn(e) == []


def test_empty_transcript_is_dropped():
    assert TurnMapper(T0).map_turn(turn(transcript="", words=[])) == []


def test_empty_end_of_turn_counts_as_finalised(caplog):
    m = TurnMapper(T0)
    with caplog.at_level("WARNING"):
        assert m.map_turn(turn(end_of_turn=True, transcript="", words=[])) == []
        m.map_turn(turn(turn_order=1, end_of_turn=True, turn_is_formatted=True))
    assert "empty transcript" in caplog.text
    assert "never finalised" not in caplog.text


def test_skipped_turn_order_is_logged(caplog):
    m = TurnMapper(T0, order_offset=10)
    with caplog.at_level("WARNING"):
        m.map_turn(turn(turn_order=0, end_of_turn=True, turn_is_formatted=True))
        assert caplog.text == ""
        m.map_turn(turn(turn_order=3, end_of_turn=True, turn_is_formatted=True))
    assert "11-12" in caplog.text


def test_missing_speaker_and_no_words():
    m = TurnMapper(T0)
    out = m.map_turn(turn(end_of_turn=True, turn_is_formatted=True, speaker_label=None, words=[]))
    assert out[0].speaker == "?"
    assert out[0].started_at == T0 and out[0].ended_at == T0


def test_order_offset_applied():
    m = TurnMapper(T0, order_offset=7)
    assert m.map_turn(turn(turn_order=2))[0].turn_order == 9


def test_map_revision():
    m = TurnMapper(T0, order_offset=3)
    ev = SpeakerRevisionEvent(
        type="SpeakerRevision",
        revisions=[
            {"turn_order": 1, "speaker_label": "B", "words": []},
            {"turn_order": 2, "speaker_label": "A", "words": []},
        ],
    )
    assert m.map_revision(ev) == [SpeakerRevision(4, "B"), SpeakerRevision(5, "A")]


def test_final_turn_carries_detected_language():
    m = TurnMapper(T0)
    out = m.map_turn(
        turn(end_of_turn=True, turn_is_formatted=True, transcript="你好。", language_code="zh")
    )
    assert out[0].language == "zh"
    out = m.map_turn(turn(turn_order=1, end_of_turn=True, turn_is_formatted=True, transcript="Hi."))
    assert out[0].language is None


def test_build_params():
    p = build_params(16000, ["Sentinel", "Alice"], "Q3 review")
    assert isinstance(p, StreamingParameters)
    assert p.sample_rate == 16000
    assert p.speech_model == "universal-3-5-pro"
    assert p.speaker_labels is True
    assert p.format_turns is True
    assert p.language_codes == ["en"]
    assert p.language_detection is None
    assert p.keyterms_prompt == ["Sentinel", "Alice"]
    assert p.prompt == "Q3 review"


def test_build_params_omits_empty_keyterms():
    p = build_params(16000, [], "")
    assert p.keyterms_prompt is None
    assert p.prompt is None


def test_build_params_multilingual_turns_on_language_detection():
    p = build_params(16000, [], "", languages=("en", "zh"))
    assert p.language_codes == ["en", "zh"]
    assert p.language_detection is True
