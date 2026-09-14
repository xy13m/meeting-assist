"""Audio sources: every chunk is mono, int16 little-endian PCM, CHUNK_MS long.

The sample rate is whatever the source reports; nothing here resamples.
"""

from __future__ import annotations

import logging
import queue
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

CHUNK_MS = 50
PREFERRED_RATE = 16000
BYTES_PER_SAMPLE = 2


class AudioSource(Protocol):
    sample_rate: int
    dropped: int

    def chunks(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class DeviceNotFound(Exception):
    pass


class FileSource:
    """Replays a mono 16-bit WAV, at real-time pace unless realtime=False."""

    def __init__(self, path: Path, realtime: bool = True) -> None:
        self._wav = wave.open(str(path), "rb")
        try:
            if self._wav.getnchannels() != 1:
                raise ValueError(f"{path}: WAV must be mono")
            if self._wav.getsampwidth() != BYTES_PER_SAMPLE:
                raise ValueError(f"{path}: WAV must be 16-bit")
            self.sample_rate = self._wav.getframerate()
        except Exception:
            self._wav.close()
            raise
        self.dropped = 0
        self._realtime = realtime
        self._closed = False

    def chunks(self) -> Iterator[bytes]:
        frames_per_chunk = self.sample_rate * CHUNK_MS // 1000
        while not self._closed:
            data = self._wav.readframes(frames_per_chunk)
            if not data:
                return
            yield data
            if self._realtime:
                time.sleep(CHUNK_MS / 1000)

    def close(self) -> None:
        self._closed = True
        self._wav.close()


def find_device(name_substring: str, devices: list[dict[str, Any]] | None = None) -> int:
    """Index of the first input device whose name contains `name_substring`."""
    if devices is None:
        import sounddevice as sd

        devices = list(sd.query_devices())
    needle = name_substring.lower()
    for index, dev in enumerate(devices):
        if needle in dev["name"].lower() and dev["max_input_channels"] > 0:
            return index
    names = ", ".join(d["name"] for d in devices) or "(none)"
    raise DeviceNotFound(
        f"No input device matching '{name_substring}'. Available: {names}.\n"
        "Install BlackHole with: brew install --cask blackhole-2ch, then create a "
        "Multi-Output Device in Audio MIDI Setup (see README)."
    )


class DeviceSource:
    """Reads an input device (BlackHole by default) through sounddevice."""

    def __init__(self, device_name: str = "BlackHole", queue_size: int = 200) -> None:
        import sounddevice as sd

        self._sd = sd
        self._device = find_device(device_name)
        self.sample_rate = self._pick_rate()
        self.dropped = 0
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=queue_size)
        self._stream: Any | None = None
        self._closed = False

    def _pick_rate(self) -> int:
        try:
            self._sd.check_input_settings(
                device=self._device, samplerate=PREFERRED_RATE, channels=1, dtype="int16"
            )
            return PREFERRED_RATE
        except Exception as exc:
            rate = int(self._sd.query_devices(self._device)["default_samplerate"])
            log.warning("device refused %d Hz (%s); using %d Hz", PREFERRED_RATE, exc, rate)
            return rate

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            log.debug("sounddevice status: %s", status)
        data = bytes(indata)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            self._queue.put_nowait(data)

    def start(self) -> None:
        self._stream = self._sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.sample_rate * CHUNK_MS // 1000,
            device=self._device,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def chunks(self) -> Iterator[bytes]:
        if self._stream is None:
            self.start()
        while not self._closed:
            try:
                yield self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

    def close(self) -> None:
        self._closed = True
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
