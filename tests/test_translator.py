import logging

from meeting_assist.events import FinalTurn, Translation
from meeting_assist.language import for_code
from meeting_assist.llm import Message
from meeting_assist.translator import Translator
from tests.conftest import T0, FakeLLM

ZH_TW = for_code("zh-TW")


def turn(order, text):
    return FinalTurn(order, "A", text, T0, T0)


def run(llm, turns, **kw) -> list[Translation]:
    out: list[Translation] = []
    tr = Translator(llm, "", ZH_TW, retry_delay=0, **kw)
    tr.start(out.append)
    for t in turns:
        tr.submit(t)
    tr.stop()
    return out


def test_system_prompt_names_the_target_and_includes_context():
    tr = Translator(FakeLLM(), "# Q3 review\nSentinel launch", ZH_TW)
    s = tr.system_prompt()
    assert "Translate it into Traditional Chinese (Taiwan)" in s
    assert "Words already in Traditional Chinese (Taiwan) stay as they are" in s
    assert s.endswith("## Meeting context\n# Q3 review\nSentinel launch")


def test_system_prompt_marks_missing_context():
    assert Translator(FakeLLM(), "  ", ZH_TW).system_prompt().endswith("(not provided)")


def test_system_prompt_for_a_generic_target():
    assert "Translate it into Japanese" in Translator(FakeLLM(), "", for_code("ja")).system_prompt()


def test_build_messages_alternates_history_then_new_turn():
    tr = Translator(FakeLLM(), "", ZH_TW, history_size=2)
    tr._history.extend([("one", "一"), ("two", "二"), ("three", "三")])
    assert tr.build_messages(turn(3, "four")) == [
        Message("user", "two"),
        Message("assistant", "二"),
        Message("user", "three"),
        Message("assistant", "三"),
        Message("user", "four"),
    ]


def test_translate_once_calls_the_llm_with_the_expected_request():
    llm = FakeLLM(["你好。"])
    tr = Translator(llm, "ctx", ZH_TW)
    assert tr.translate_once(turn(0, "Hello.")) == "你好。"
    system, messages, max_tokens = llm.calls[0]
    assert system == tr.system_prompt()
    assert messages[-1] == Message("user", "Hello.")
    assert max_tokens == 400


def test_worker_translates_in_order_and_updates_history():
    llm = FakeLLM(["一", "二"])
    out = run(llm, [turn(0, "one"), turn(1, "two")])
    assert out == [Translation(0, "一"), Translation(1, "二")]
    assert llm.calls[1][1] == [
        Message("user", "one"),
        Message("assistant", "一"),
        Message("user", "two"),
    ]


def test_worker_retries_once_then_reports_error():
    llm = FakeLLM([RuntimeError("rate limited"), RuntimeError("still down"), "三"])
    out = run(llm, [turn(0, "one"), turn(1, "three")])
    assert out == [Translation(0, "", error="still down"), Translation(1, "三")]
    # A failed turn leaves no trace in the history sent with the next one.
    assert llm.calls[2][1] == [Message("user", "three")]


def test_retry_success_on_second_attempt():
    out = run(FakeLLM([RuntimeError("blip"), "一"]), [turn(0, "one")])
    assert out == [Translation(0, "一")]


def test_backlog_counts_queued_turns():
    tr = Translator(FakeLLM(), "", ZH_TW)
    tr.submit(turn(0, "one"))
    tr.submit(turn(1, "two"))
    assert tr.backlog() == 2


def test_worker_survives_callback_exception(caplog):
    llm = FakeLLM(["一", "二"])
    out = []
    calls = [0]

    def failing_callback(tr):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("callback crashed")
        out.append(tr)

    tr = Translator(llm, "", ZH_TW, retry_delay=0)
    with caplog.at_level(logging.ERROR):
        tr.start(failing_callback)
        tr.submit(turn(0, "one"))
        tr.submit(turn(1, "two"))
        tr.stop()
    assert out == [Translation(1, "二")]
    assert "callback crashed" in caplog.text
    assert not tr.is_alive()
