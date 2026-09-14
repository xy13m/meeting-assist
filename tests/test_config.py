import logging
from pathlib import Path

import pytest

from meeting_assist.config import ConfigError, Keys, Settings, config_path, load_settings


def load(tmp_path: Path, env: dict | None = None, overrides: dict | None = None) -> Settings:
    return load_settings(
        overrides or {},
        env=env or {},
        config_file=tmp_path / "config.toml",
        cwd=tmp_path,
    )


def test_defaults_when_nothing_is_configured(tmp_path: Path):
    s = load(tmp_path)
    assert s == Settings(
        keys=Keys(assemblyai=None, anthropic=None, openai=None),
        model="claude-haiku-4-5",
        target="zh-TW",
        device="BlackHole",
        out=Path("meetings"),
    )


def test_config_file_values_are_read(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[keys]\nassemblyai = "a"\nanthropic = "b"\n\n'
        '[defaults]\nmodel = "gpt-4.1-mini"\ntarget = "ja"\ndevice = "Loopback"\nout = "~/m"\n'
    )
    s = load(tmp_path)
    assert s.keys == Keys(assemblyai="a", anthropic="b", openai=None)
    assert s.model == "gpt-4.1-mini"
    assert s.target == "ja"
    assert s.device == "Loopback"
    assert s.out == Path("~/m").expanduser()


def test_environment_beats_config_file(tmp_path: Path):
    (tmp_path / "config.toml").write_text('[keys]\nassemblyai = "file"\n[defaults]\nmodel = "m1"\n')
    s = load(tmp_path, env={"ASSEMBLYAI_API_KEY": "env", "MEETING_ASSIST_MODEL": "m2"})
    assert s.keys.assemblyai == "env"
    assert s.model == "m2"


def test_all_environment_variable_names(tmp_path: Path):
    env = {
        "ASSEMBLYAI_API_KEY": "a",
        "ANTHROPIC_API_KEY": "b",
        "OPENAI_API_KEY": "c",
        "MEETING_ASSIST_MODEL": "m",
        "MEETING_ASSIST_TARGET": "t",
        "MEETING_ASSIST_DEVICE": "d",
        "MEETING_ASSIST_OUT": "/o",
    }
    s = load(tmp_path, env=env)
    assert s == Settings(Keys("a", "b", "c"), model="m", target="t", device="d", out=Path("/o"))


def test_dotenv_in_cwd_is_read_but_real_environment_wins(tmp_path: Path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv\nANTHROPIC_API_KEY=dotenv\n")
    s = load(tmp_path, env={"OPENAI_API_KEY": "real"})
    assert s.keys.openai == "real"
    assert s.keys.anthropic == "dotenv"


def test_overrides_beat_environment_unless_none(tmp_path: Path):
    env = {"MEETING_ASSIST_MODEL": "env", "MEETING_ASSIST_OUT": "env-out"}
    s = load(tmp_path, env=env, overrides={"model": "flag", "out": None})
    assert s.model == "flag"
    assert s.out == Path("env-out")


def test_override_out_accepts_path(tmp_path: Path):
    s = load(tmp_path, overrides={"out": tmp_path / "x"})
    assert s.out == tmp_path / "x"


def test_unknown_config_keys_are_ignored_with_a_warning(tmp_path: Path, caplog):
    (tmp_path / "config.toml").write_text('[defaults]\nmodle = "typo"\n[other]\nx = 1\n')
    with caplog.at_level(logging.WARNING):
        s = load(tmp_path)
    assert s.model == "claude-haiku-4-5"
    assert "modle" in caplog.text
    assert "other" in caplog.text


def test_malformed_config_file_is_a_config_error(tmp_path: Path):
    (tmp_path / "config.toml").write_text("[keys\n")
    with pytest.raises(ConfigError, match="config.toml"):
        load(tmp_path)


def test_config_path_honours_environment_override(tmp_path: Path):
    assert config_path({"MEETING_ASSIST_CONFIG": str(tmp_path / "c.toml")}) == tmp_path / "c.toml"
    assert config_path({}) == Path("~/.config/meeting-assist/config.toml").expanduser()
