"""End-to-end run against the real APIs. Skipped unless both keys are set.

Run: uv run pytest -m integration -v
Cost: a few cents (a ~10 s AssemblyAI session plus a few LLM calls).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from meeting_assist import cli

pytestmark = pytest.mark.integration

needs_keys = pytest.mark.skipif(
    not (os.environ.get("ASSEMBLYAI_API_KEY") and os.environ.get("ANTHROPIC_API_KEY")),
    reason="needs ASSEMBLYAI_API_KEY and ANTHROPIC_API_KEY",
)


def make_speech_wav(path: Path) -> None:
    """Use macOS text-to-speech to build a short English WAV (16 kHz mono int16)."""
    aiff = path.with_suffix(".aiff")
    text = "Hello everyone. Let's start with the Sentinel launch date."
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(path)],
        check=True,
    )


@needs_keys
def test_wav_end_to_end_then_summarize(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEETING_ASSIST_CONFIG", str(tmp_path / "no-config.toml"))
    wav = tmp_path / "speech.wav"
    make_speech_wav(wav)
    ctx = tmp_path / "meeting.md"
    ctx.write_text("# Sentinel launch sync\n\n## Keyterms\n- Sentinel\n")
    out = tmp_path / "m"
    assert cli.main(["listen", "--context", str(ctx), "--wav", str(wav), "--out", str(out)]) == 0
    meeting_dir = next(out.iterdir())
    jsonl = (meeting_dir / "transcript.jsonl").read_text().splitlines()
    lines = [json.loads(line) for line in jsonl]
    turns = [rec for rec in lines if rec["type"] == "turn"]
    translations = [rec for rec in lines if rec["type"] == "translation" and rec["error"] is None]
    assert turns, "expected at least one turn"
    assert translations, "expected at least one successful translation"
    assert (meeting_dir / "transcript.md").read_text().strip()
    assert (meeting_dir / "meeting-assist.log").exists()

    assert cli.main(["summarize", str(meeting_dir)]) == 0
    summary = (meeting_dir / "summary.md").read_text()
    assert "## Summary" in summary and "## Action items" in summary
