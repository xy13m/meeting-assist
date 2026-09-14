import sys
import wave
from pathlib import Path

import pytest

from meeting_assist.audio import CHUNK_MS, DeviceNotFound, DeviceSource, FileSource, find_device
from tests.conftest import write_wav


def test_file_source_yields_50ms_int16_chunks(tmp_path: Path):
    p = tmp_path / "a.wav"
    write_wav(p, 16000, 16000)  # one second
    src = FileSource(p, realtime=False)
    assert src.sample_rate == 16000
    chunks = list(src.chunks())
    assert len(chunks) == 1000 // CHUNK_MS
    assert all(len(c) == 16000 * CHUNK_MS // 1000 * 2 for c in chunks)
    assert src.dropped == 0


def test_file_source_last_partial_chunk_is_kept(tmp_path: Path):
    p = tmp_path / "a.wav"
    write_wav(p, 16000, 900)  # 56.25 ms -> one full chunk + one partial
    chunks = list(FileSource(p, realtime=False).chunks())
    assert len(chunks) == 2
    assert len(chunks[1]) == 100 * 2


def test_file_source_close_stops_iteration(tmp_path: Path):
    p = tmp_path / "a.wav"
    write_wav(p, 16000, 16000)
    src = FileSource(p, realtime=False)
    it = src.chunks()
    next(it)
    src.close()
    assert list(it) == []


def _write_stereo(p: Path) -> None:
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 4)


def test_file_source_rejects_stereo(tmp_path: Path):
    p = tmp_path / "s.wav"
    _write_stereo(p)
    with pytest.raises(ValueError, match="mono"):
        FileSource(p)


def test_file_source_rejects_8_bit(tmp_path: Path):
    p = tmp_path / "s.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(16000)
        w.writeframes(b"\x00" * 4)
    with pytest.raises(ValueError, match="16-bit"):
        FileSource(p)


def test_file_source_closes_handle_on_validation_failure(tmp_path: Path, monkeypatch):
    p = tmp_path / "s.wav"
    _write_stereo(p)
    closed = []
    original_open = wave.open

    def tracked_open(*args, **kwargs):
        wav_obj = original_open(*args, **kwargs)
        original_close = wav_obj.close

        def tracked_close():
            closed.append(True)
            original_close()

        wav_obj.close = tracked_close
        return wav_obj

    monkeypatch.setattr("meeting_assist.audio.wave.open", tracked_open)
    with pytest.raises(ValueError, match="mono"):
        FileSource(p)
    assert closed


DEVICES = [
    {"name": "MacBook Pro Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
    {"name": "BlackHole 2ch", "max_input_channels": 2, "default_samplerate": 48000.0},
    {"name": "RØDE PodMic USB", "max_input_channels": 1, "default_samplerate": 48000.0},
]


def test_find_device_matches_input_device_by_substring():
    assert find_device("blackhole", DEVICES) == 1


def test_find_device_ignores_output_only_devices():
    with pytest.raises(DeviceNotFound):
        find_device("speakers", DEVICES)


def test_find_device_missing_lists_devices_and_hints_at_install():
    pattern = r"(?s)RØDE PodMic USB.*brew install --cask blackhole-2ch"
    with pytest.raises(DeviceNotFound, match=pattern):
        find_device("BlackHole", DEVICES[2:])


class FakeSounddevice:
    """Enough of the sounddevice module for DeviceSource."""

    def __init__(self, refuse_16k: bool = False) -> None:
        self.refuse_16k = refuse_16k
        self.streams: list[FakeStream] = []

    def query_devices(self, device=None):
        return DEVICES if device is None else DEVICES[device]

    def check_input_settings(self, device, samplerate, channels, dtype):
        if self.refuse_16k and samplerate == 16000:
            raise ValueError("unsupported")

    def RawInputStream(self, **kw):  # noqa: N802 - mirrors the sounddevice API
        stream = FakeStream(kw)
        self.streams.append(stream)
        return stream


class FakeStream:
    def __init__(self, kw):
        self.kw = kw
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_sd(monkeypatch):
    sd = FakeSounddevice()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_device_source_prefers_16k_and_opens_a_mono_int16_stream(fake_sd):
    src = DeviceSource("BlackHole")
    assert src.sample_rate == 16000
    src.start()
    it = src.chunks()
    stream = fake_sd.streams[0]
    assert stream.kw["device"] == 1
    assert stream.kw["channels"] == 1
    assert stream.kw["dtype"] == "int16"
    assert stream.kw["blocksize"] == 16000 * CHUNK_MS // 1000
    stream.kw["callback"](b"\x01\x02", 1, None, None)
    assert next(it) == b"\x01\x02"
    src.close()
    assert stream.closed
    assert list(it) == []


def test_device_source_falls_back_to_the_device_rate(monkeypatch, caplog):
    sd = FakeSounddevice(refuse_16k=True)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    with caplog.at_level("WARNING"):
        src = DeviceSource("BlackHole")
    assert src.sample_rate == 48000
    assert "48000" in caplog.text


def test_device_source_drops_oldest_chunk_when_queue_is_full(fake_sd):
    src = DeviceSource("BlackHole", queue_size=2)
    src.start()
    cb = fake_sd.streams[0].kw["callback"]
    cb(b"a", 1, None, None)
    cb(b"b", 1, None, None)
    cb(b"c", 1, None, None)
    assert src.dropped == 1
    it = src.chunks()
    assert next(it) == b"b"
    assert next(it) == b"c"
    src.close()


def test_device_source_status_is_logged_at_debug(fake_sd, caplog):
    src = DeviceSource("BlackHole")
    src.start()
    with caplog.at_level("DEBUG"):
        fake_sd.streams[0].kw["callback"](b"a", 1, None, "input overflow")
    assert "overflow" in caplog.text
    src.close()


def test_device_source_missing_device_raises(fake_sd):
    with pytest.raises(DeviceNotFound):
        DeviceSource("Nonexistent")
