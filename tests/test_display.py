import io

from rich.console import Console

from meeting_assist.display import Display, render_turn
from meeting_assist.events import FinalTurn, PartialTurn, Translation
from tests.conftest import T0


def make_console():
    buf = io.StringIO()
    return Console(file=buf, width=80, force_terminal=False, color_system=None), buf


def test_render_turn_with_and_without_translation():
    turn = FinalTurn(0, "A", "Hello there.", T0, T0)
    assert render_turn(turn, None, "cyan").plain == "10:00:00  A  Hello there.\n          …"
    assert render_turn(turn, None, "cyan", awaiting=False).plain == "10:00:00  A  Hello there."
    done = render_turn(turn, Translation(0, "你好。"), "cyan").plain
    assert done == "10:00:00  A  Hello there.\n          你好。"
    failed = render_turn(turn, Translation(0, "", error="boom"), "cyan").plain
    assert failed.endswith("(translation failed: boom)")


def test_unknown_speaker_labels_render_as_question_mark():
    turn = FinalTurn(0, "PENDING", "Hi.", T0, T0)
    assert render_turn(turn, None, "dim", awaiting=False).plain == "10:00:00  ?  Hi."


def test_speaker_styles_assigned_in_order_of_appearance():
    d = Display(make_console()[0])
    assert d.speaker_style("B") == "cyan"
    assert d.speaker_style("A") == "green"
    assert d.speaker_style("B") == "cyan"
    assert d.speaker_style("PENDING") == "dim"
    assert d.speaker_style("?") == "dim"


def test_completed_turn_is_printed_once_translation_arrives():
    console, buf = make_console()
    d = Display(console)
    d.start()
    d.show_turn(FinalTurn(0, "A", "Hello there.", T0, T0))
    assert "Hello there." not in buf.getvalue().replace("…", "")  # still pending
    d.show_translation(Translation(0, "你好。"))
    d.stop()
    out = buf.getvalue()
    assert "10:00:00  A  Hello there." in out
    assert "你好。" in out


def test_untranslated_turn_is_printed_without_pending_marker():
    console, buf = make_console()
    d = Display(console)
    d.start()
    d.show_turn(FinalTurn(0, "A", "你好。", T0, T0))
    d.show_untranslated(0)
    d.stop()
    out = buf.getvalue()
    assert "10:00:00  A  你好。" in out
    assert "…" not in out


def test_stop_flushes_pending_turns():
    console, buf = make_console()
    d = Display(console)
    d.start()
    d.show_turn(FinalTurn(0, "A", "Hello there.", T0, T0))
    d.stop()
    out = buf.getvalue()
    assert "10:00:00  A  Hello there." in out
    assert "…" in out


def test_status_line_shows_hour_minute_second(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("meeting_assist.display.time.monotonic", lambda: clock["t"])
    console, _ = make_console()
    d = Display(console)
    d.start()
    clock["t"] = 1000.0 + 3735  # 1h 2m 15s later
    d.set_status(connection="connected", backlog=0, dropped=0)
    assert d.live_text().startswith("1:02:15  ")
    d.stop()


def test_partial_and_status_render_in_live_area():
    console, buf = make_console()
    d = Display(console)
    d.start()
    d.show_partial(PartialTurn(1, "B", "so the plan"))
    d.set_status(connection="connected", backlog=2, dropped=0)
    text = d.live_text()
    assert "B  so the plan" in text
    assert "connected" in text and "backlog 2" in text and "dropped 0" in text
    d.show_turn(FinalTurn(1, "B", "So the plan is set.", T0, T0))
    assert "so the plan" not in d.live_text()  # partial replaced by its final turn
    d.stop()
