"""EchoFilter with a fake clock; no threads except the ticker test."""

import time
from collections import Counter
from datetime import datetime, timedelta

from meeting_assist.echo import EchoFilter, echo_score, tokens
from meeting_assist.events import (
    ME_SPEAKER,
    MIC_ORDER_BASE,
    FinalTurn,
    PartialTurn,
    SpeakerRevision,
)
from tests.conftest import T0


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def make(enabled: bool = True):
    out: list = []
    drops: list[int] = []
    clock = Clock()
    f = EchoFilter(out.append, enabled=enabled, clock=clock, on_drop=drops.append)
    return f, out, clock, drops


def sys_turn(order: int, text: str, start: float, end: float) -> FinalTurn:
    return FinalTurn(order, "A", text, at(start), at(end))


def mic_turn(n: int, text: str, start: float, end: float) -> FinalTurn:
    return FinalTurn(MIC_ORDER_BASE + n, ME_SPEAKER, text, at(start), at(end))


def test_tokens_split_latin_words_and_cjk_characters():
    assert tokens("Hello, hello world! 只有license") == Counter(
        {"hello": 2, "world": 1, "只": 1, "有": 1, "license": 1}
    )


def test_echo_score_counts_each_token_at_most_as_often_as_candidates_have_it():
    assert echo_score("yes yes yes no", ["yes"]) == 0.25
    assert echo_score("", ["yes"]) == 0.0


def test_pass_through_forwards_everything_immediately():
    f, out, _, _ = make(enabled=False)
    partial = PartialTurn(MIC_ORDER_BASE, ME_SPEAKER, "hi")
    mine = mic_turn(0, "Hi.", 0, 1)
    theirs = sys_turn(0, "Hi.", 0, 1)
    f.on_mic(partial)
    f.on_mic(mine)
    f.on_system(theirs)
    assert out == [partial, mine, theirs]


def test_mic_turn_matching_a_system_turn_is_dropped(caplog):
    f, out, clock, drops = make()
    theirs = sys_turn(0, "We ship on Friday.", 0, 2)
    f.on_system(theirs)
    clock.now = at(2.1)
    with caplog.at_level("WARNING"):
        f.on_mic(mic_turn(0, "we ship on friday", 0.2, 2.1))
    assert out == [theirs]
    assert drops == [1]
    assert "echo" in caplog.text and "we ship on friday" in caplog.text


def test_mic_turn_is_dropped_when_the_system_turn_arrives_later():
    f, out, clock, drops = make()
    clock.now = at(2)
    f.on_mic(mic_turn(0, "We ship on Friday.", 0, 2))
    assert out == []
    clock.now = at(3)
    theirs = sys_turn(0, "We ship on Friday.", 0, 2.5)
    f.on_system(theirs)
    assert out == [theirs]
    assert drops == [1]


def test_unmatched_mic_turn_is_forwarded_after_hold():
    f, out, clock, drops = make()
    mine = mic_turn(0, "Sounds good to me.", 0, 1)
    clock.now = at(1)
    f.on_mic(mine)
    clock.now = at(3.9)
    f.check()
    assert out == []
    clock.now = at(4.0)
    f.check()
    assert out == [mine]
    assert drops == []


def test_in_progress_system_partial_counts_as_a_match():
    f, out, clock, drops = make()
    partial = PartialTurn(0, "A", "so the release moves to")
    f.on_system(partial)
    clock.now = at(2)
    f.on_mic(mic_turn(0, "So the release moves", 0, 2))
    assert out == [partial]
    assert drops == [1]


def test_partials_older_than_the_latest_system_final_are_forgotten():
    f, out, clock, drops = make()
    f.on_system(PartialTurn(0, "A", "so the release moves to"))
    f.on_system(sys_turn(1, "Okay.", 5, 6))
    clock.now = at(30)
    mine = mic_turn(0, "So the release moves", 28, 30)
    f.on_mic(mine)
    clock.now = at(33)
    f.check()
    assert out[-1] == mine
    assert drops == []


def test_short_turn_outside_the_time_window_is_kept():
    f, out, clock, drops = make()
    f.on_system(sys_turn(0, "Yes.", 0, 1))
    clock.now = at(10)
    mine = mic_turn(0, "Yes.", 9, 10)
    f.on_mic(mine)
    clock.now = at(13)
    f.check()
    assert out[-1] == mine
    assert drops == []


def test_low_overlap_is_kept():
    f, out, clock, drops = make()
    f.on_system(sys_turn(0, "We ship on Friday.", 0, 2))
    clock.now = at(2)
    mine = mic_turn(0, "I disagree, we need two more weeks.", 0, 2)
    f.on_mic(mine)
    clock.now = at(5)
    f.check()
    assert out[-1] == mine
    assert drops == []


def test_cjk_echo_is_dropped():
    f, out, clock, drops = make()
    f.on_system(sys_turn(0, "我們下週五上線。", 0, 2))
    clock.now = at(2)
    f.on_mic(mic_turn(0, "下週五上線", 0.5, 2))
    assert drops == [1]


def test_mic_partials_are_suppressed_while_filtering():
    f, out, _, _ = make()
    f.on_mic(PartialTurn(MIC_ORDER_BASE, ME_SPEAKER, "hi"))
    assert out == []


def test_speaker_revisions_pass_through():
    f, out, _, _ = make()
    rev = SpeakerRevision(0, "B")
    f.on_system(rev)
    f.on_mic(rev)
    assert out == [rev, rev]


def test_close_forwards_held_turns_and_is_idempotent():
    f, out, clock, _ = make()
    mine = mic_turn(0, "Thanks.", 0, 1)
    clock.now = at(1)
    f.on_mic(mine)
    f.close()
    f.close()
    assert out == [mine]


def test_ticker_releases_held_turns_without_new_events():
    out: list = []
    f = EchoFilter(out.append, enabled=True, hold=0.1, tick=0.02)
    now = datetime.now()
    mine = FinalTurn(MIC_ORDER_BASE, ME_SPEAKER, "Thanks.", now, now)
    f.start()
    f.on_mic(mine)
    deadline = time.monotonic() + 2
    while not out and time.monotonic() < deadline:
        time.sleep(0.01)
    f.close()
    assert out == [mine]


def test_reply_that_repeats_the_question_is_kept():
    f, out, clock, drops = make()
    f.on_system(sys_turn(0, "Can we ship on Friday, yes or no?", 0, 3))
    clock.now = at(4.6)
    mine = mic_turn(0, "Yes, we can ship on Friday.", 3.6, 4.6)
    f.on_mic(mine)
    clock.now = at(7.6)
    f.check()
    assert out[-1] == mine
    assert drops == []
