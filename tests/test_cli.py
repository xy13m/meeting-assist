import json
from pathlib import Path

import pytest
from assemblyai.streaming.v3 import StreamingError, StreamingEvents

from meeting_assist import cli
from meeting_assist.audio import DeviceNotFound
from meeting_assist.stt.assemblyai import AssemblyAITranscriber
from tests.conftest import FakeLLM, write_wav
from tests.stt.fakes import FakeClient


@pytest.fixture
def env(monkeypatch, tmp_path: Path):
    """Isolated config: no config file, no .env, keys set, cwd in tmp_path."""
    for name in ("MEETING_ASSIST_MODEL", "MEETING_ASSIST_TARGET", "MEETING_ASSIST_OUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEETING_ASSIST_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "b")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM(["你好。"] * 20)
    monkeypatch.setattr(cli, "create_llm", lambda model, keys: llm)
    return llm


def test_parse_args_listen_defaults():
    ns = cli.parse_args(["listen"])
    assert ns.command == "listen"
    assert ns.context is None
    assert ns.device is None
    assert ns.wav is None
    assert ns.out is None
    assert ns.model is None
    assert ns.target is None
    assert ns.no_keyterms is False
    assert ns.languages == ("en",)


def test_parse_args_listen_flags():
    ns = cli.parse_args(
        [
            "listen",
            "--context",
            "m.md",
            "--wav",
            "a.wav",
            "--no-keyterms",
            "--out",
            "x",
            "--model",
            "gpt-4.1-mini",
            "--target",
            "ja",
            "--device",
            "Loopback",
            "--languages",
            " en , zh ",
        ]
    )
    assert ns.context == Path("m.md")
    assert ns.wav == Path("a.wav")
    assert ns.no_keyterms is True
    assert ns.out == Path("x")
    assert ns.model == "gpt-4.1-mini"
    assert ns.target == "ja"
    assert ns.device == "Loopback"
    assert ns.languages == ("en", "zh")


def test_parse_args_rejects_empty_languages():
    with pytest.raises(SystemExit):
        cli.parse_args(["listen", "--languages", ","])


def test_no_command_prints_help_and_exits_1(capsys):
    assert cli.main([]) == 1
    assert "listen" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "meeting-assist 0.1.0" in capsys.readouterr().out


def test_listen_requires_assemblyai_key(env, monkeypatch, capsys):
    monkeypatch.delenv("ASSEMBLYAI_API_KEY")
    assert cli.main(["listen"]) == 1
    assert "ASSEMBLYAI_API_KEY" in capsys.readouterr().err


def test_listen_requires_the_provider_key(env, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert cli.main(["listen"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_listen_reports_unknown_model(env, capsys):
    assert cli.main(["listen", "--model", "mistral-large"]) == 1
    assert "mistral-large" in capsys.readouterr().err


def test_listen_reports_bad_config_file(env, monkeypatch, capsys):
    bad = env / "bad.toml"
    bad.write_text("[keys\n")
    monkeypatch.setenv("MEETING_ASSIST_CONFIG", str(bad))
    assert cli.main(["listen"]) == 1
    assert "bad.toml" in capsys.readouterr().err


def test_listen_reports_missing_context_file(env, fake_llm, capsys):
    missing = env / "does-not-exist.md"
    assert cli.main(["listen", "--context", str(missing)]) == 1
    err = capsys.readouterr().err
    assert str(missing) in err and "Traceback" not in err


def test_listen_reports_missing_device(env, fake_llm, monkeypatch, capsys):
    def boom(*a, **k):
        raise DeviceNotFound("No input device matching 'BlackHole'")

    monkeypatch.setattr(cli, "DeviceSource", boom)
    assert cli.main(["listen"]) == 1
    assert "No input device" in capsys.readouterr().err
    assert not (env / "meetings").exists()  # nothing created before the source opened


def test_listen_reports_bad_wav_file(env, fake_llm, capsys):
    not_wav = env / "notes.txt"
    not_wav.write_text("this is not a wav file")
    assert cli.main(["listen", "--wav", str(not_wav)]) == 1
    err = capsys.readouterr().err
    assert "notes.txt" in err and "Traceback" not in err


def test_listen_runs_without_context_and_skips_keyterm_derivation(env, fake_llm, monkeypatch):
    def must_not_derive(*a, **k):
        raise AssertionError("derive_keyterms must not run without a context file")

    monkeypatch.setattr(cli, "derive_keyterms", must_not_derive)
    monkeypatch.setattr(cli, "create_transcriber", make_factory(FakeClient))
    wav = env / "a.wav"
    write_wav(wav, 16000, 1600)
    assert cli.main(["listen", "--wav", str(wav)]) == 0


def make_factory(client_cls):
    def factory(settings, client_factory=None):
        return AssemblyAITranscriber(settings, client_factory=client_cls)

    return factory


def test_listen_no_keyterms_flag_skips_derivation(env, fake_llm, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "derive_keyterms", lambda text, llm: calls.append(text) or [])
    monkeypatch.setattr(cli, "create_transcriber", make_factory(FakeClient))
    ctx = env / "m.md"
    ctx.write_text("# Sync\nNotes.\n")
    wav = env / "a.wav"
    write_wav(wav, 16000, 1600)
    assert cli.main(["listen", "--wav", str(wav), "--context", str(ctx), "--no-keyterms"]) == 0
    assert calls == []
    assert cli.main(["listen", "--wav", str(wav), "--context", str(ctx)]) == 0
    assert calls == ["# Sync\nNotes.\n"]


def test_listen_writes_meeting_dir_log_and_meta(env, fake_llm, monkeypatch, capsys):
    monkeypatch.setattr(cli, "create_transcriber", make_factory(FakeClient))
    wav = env / "a.wav"
    write_wav(wav, 16000, 1600)
    out = env / "out"
    assert cli.main(["listen", "--wav", str(wav), "--out", str(out), "--target", "ja"]) == 0
    meeting_dir = next(out.iterdir())
    assert (meeting_dir / "meeting-assist.log").exists()
    meta = json.loads((meeting_dir / "transcript.jsonl").read_text().splitlines()[0])
    assert meta["target"] == "ja"
    assert meta["languages"] == ["en"]
    err = capsys.readouterr().err
    assert str(meeting_dir) in err


def test_listen_reports_early_stop_and_exits_2(env, fake_llm, monkeypatch, capsys):
    class AlwaysFailingClient(FakeClient):
        def connect(self, params):
            self.handlers[StreamingEvents.Error](self, StreamingError("boom"))

    def factory(settings, client_factory=None):
        from dataclasses import replace

        return AssemblyAITranscriber(
            replace(settings, max_reconnects=0), client_factory=AlwaysFailingClient
        )

    monkeypatch.setattr(cli, "create_transcriber", factory)
    wav = env / "a.wav"
    write_wav(wav, 16000, 1600)
    assert cli.main(["listen", "--wav", str(wav)]) == 2
    err = capsys.readouterr().err
    assert "Stopped early" in err and "gave up reconnecting" in err
