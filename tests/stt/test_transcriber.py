import pytest
from assemblyai.streaming.v3 import (
    SpeakerRevisionEvent,
    StreamingError,
    StreamingEvents,
    TerminationEvent,
)

from meeting_assist.events import FinalTurn, SpeakerRevision
from meeting_assist.stt import TranscriberSettings, create_transcriber
from meeting_assist.stt.assemblyai import AssemblyAITranscriber
from tests.stt.fakes import FakeClient, ListSource, StopEventClient, turn


@pytest.fixture(autouse=True)
def clear_instances():
    FakeClient.instances.clear()


def settings(**kw) -> TranscriberSettings:
    base = dict(api_key="key", sample_rate=16000, keyterms=[], prompt="", reconnect_delay=0)
    base.update(kw)
    return TranscriberSettings(**base)


def make(client_factory=FakeClient, **kw) -> AssemblyAITranscriber:
    return AssemblyAITranscriber(settings(**kw), client_factory=client_factory)


def termination() -> TerminationEvent:
    return TerminationEvent(
        type="Termination", audio_duration_seconds=5, session_duration_seconds=5
    )


def test_create_transcriber_returns_the_assemblyai_implementation():
    t = create_transcriber(settings())
    assert isinstance(t, AssemblyAITranscriber)
    assert t.abort_reason is None


def test_run_streams_all_chunks_and_terminates():
    events, statuses = [], []
    make().run(ListSource(3), events.append, statuses.append)
    client = FakeClient.instances[0]
    assert client.chunks == [b"\x00", b"\x01", b"\x02"]
    assert client.disconnects == [True]
    assert statuses[0].startswith("connected abcdef12")


def test_run_reconnects_after_error_and_offsets_turn_order():
    events, statuses = [], []

    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            c.handlers[StreamingEvents.Turn](
                c, turn(turn_order=4, end_of_turn=True, turn_is_formatted=True)
            )
            yield b"a"
            c.handlers[StreamingEvents.Error](c, StreamingError("boom"))
            yield b"b"  # triggers reconnect before this chunk is sent
            c2 = FakeClient.instances[1]
            c2.handlers[StreamingEvents.Turn](
                c2, turn(turn_order=0, end_of_turn=True, turn_is_formatted=True)
            )
            yield b"c"

    make().run(Source(0), events.append, statuses.append)
    assert len(FakeClient.instances) == 2
    assert FakeClient.instances[0].chunks == [b"a"]
    assert FakeClient.instances[1].chunks == [b"b", b"c"]
    assert [e.turn_order for e in events if isinstance(e, FinalTurn)] == [4, 5]
    assert any(s.startswith("reconnecting (1/5)") for s in statuses)


def test_run_keeps_turn_order_unique_across_reconnect_before_any_turn():
    """A session that errors before any Turn must not reset the high-water mark."""
    events = []

    class Source(ListSource):
        def chunks(self):
            c0 = FakeClient.instances[0]
            c0.handlers[StreamingEvents.Turn](
                c0, turn(turn_order=0, end_of_turn=True, turn_is_formatted=True)
            )
            c0.handlers[StreamingEvents.Turn](
                c0, turn(turn_order=4, end_of_turn=True, turn_is_formatted=True)
            )
            yield b"a"
            c0.handlers[StreamingEvents.Error](c0, StreamingError("boom"))
            yield b"b"
            c1 = FakeClient.instances[1]
            c1.handlers[StreamingEvents.Error](c1, StreamingError("boom"))
            yield b"c"
            c2 = FakeClient.instances[2]
            c2.handlers[StreamingEvents.Turn](
                c2, turn(turn_order=0, end_of_turn=True, turn_is_formatted=True)
            )
            yield b"d"

    make().run(Source(0), events.append, lambda s: None)
    assert len(FakeClient.instances) == 3
    assert [e.turn_order for e in events if isinstance(e, FinalTurn)] == [0, 4, 5]


class AlwaysFailingClient(FakeClient):
    def connect(self, params):
        self.handlers[StreamingEvents.Error](self, StreamingError("boom"))


def test_run_gives_up_after_max_reconnects_and_records_abort_reason():
    statuses = []
    t = make(AlwaysFailingClient, max_reconnects=1)
    t.run(ListSource(3), lambda e: None, statuses.append)
    assert len(FakeClient.instances) == 2
    assert statuses[-1] == "gave up reconnecting"
    assert t.abort_reason == "gave up reconnecting after 1 attempts"


def test_reconnect_budget_counts_consecutive_failures_only():
    statuses = []

    class Source(ListSource):
        def chunks(self):
            c0 = FakeClient.instances[0]
            c0.handlers[StreamingEvents.Error](c0, StreamingError("boom"))
            yield b"a"  # reconnect to session 2
            c1 = FakeClient.instances[1]
            yield b"b"  # session 2 is healthy: Begin reset the streak
            c1.handlers[StreamingEvents.Error](c1, StreamingError("boom"))
            yield b"c"  # second reconnect, still within budget

    make(max_reconnects=1).run(Source(0), lambda e: None, statuses.append)
    assert len(FakeClient.instances) == 3
    assert statuses.count("reconnecting (1/1)") == 2
    assert "gave up reconnecting" not in statuses


def test_termination_event_triggers_reconnect_when_not_stopped():
    statuses = []

    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            yield b"a"
            c.handlers[StreamingEvents.Termination](c, termination())
            yield b"b"
            yield b"c"

    make().run(Source(0), lambda e: None, statuses.append)
    assert len(FakeClient.instances) == 2
    assert FakeClient.instances[0].chunks == [b"a"]
    assert FakeClient.instances[1].chunks == [b"b", b"c"]
    assert any(s.startswith("reconnecting (1/5)") for s in statuses)


def test_termination_after_stop_does_not_reconnect():
    t = make()

    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            yield b"a"
            t.stop()
            c.handlers[StreamingEvents.Termination](c, termination())
            yield b"b"

    t.run(Source(0), lambda e: None, lambda s: None)
    assert len(FakeClient.instances) == 1
    assert FakeClient.instances[0].chunks == [b"a"]


def test_silent_close_via_private_stop_event_triggers_reconnect():
    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            yield b"a"
            c._stop_event.set()
            yield b"b"
            yield b"c"

    make(StopEventClient).run(Source(0), lambda e: None, lambda s: None)
    assert len(FakeClient.instances) == 2
    assert FakeClient.instances[0].chunks == [b"a"]
    assert FakeClient.instances[1].chunks == [b"b", b"c"]


def test_held_unformatted_turn_is_emitted_at_shutdown():
    events = []

    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            c.handlers[StreamingEvents.Turn](
                c, turn(turn_order=0, end_of_turn=True, transcript="bye now")
            )
            yield b"a"

    make().run(Source(0), events.append, lambda s: None)
    finals = [e for e in events if isinstance(e, FinalTurn)]
    assert [(e.turn_order, e.text) for e in finals] == [(0, "bye now")]
    assert FakeClient.instances[0].disconnects == [True]


def test_held_unformatted_turn_is_emitted_before_reconnect_and_keeps_order_unique():
    events = []

    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            c.handlers[StreamingEvents.Turn](
                c, turn(turn_order=2, end_of_turn=True, transcript="cut off")
            )
            c.handlers[StreamingEvents.Error](c, StreamingError("boom"))
            yield b"a"
            c2 = FakeClient.instances[1]
            c2.handlers[StreamingEvents.Turn](
                c2, turn(turn_order=0, end_of_turn=True, turn_is_formatted=True)
            )

    make().run(Source(0), events.append, lambda s: None)
    finals = [(e.turn_order, e.text) for e in events if isinstance(e, FinalTurn)]
    assert finals == [(2, "cut off"), (3, "hello there")]


def test_speaker_revisions_are_forwarded_with_offset():
    events = []

    class Source(ListSource):
        def chunks(self):
            c = FakeClient.instances[0]
            yield b"a"
            c.handlers[StreamingEvents.SpeakerRevision](
                c,
                SpeakerRevisionEvent(
                    revisions=[{"turn_order": 0, "speaker_label": "B", "words": []}]
                ),
            )

    make().run(Source(0), events.append, lambda s: None)
    assert events == [SpeakerRevision(0, "B")]
