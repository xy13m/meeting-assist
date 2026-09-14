"""Layered settings: built-in defaults < config.toml < .env < environment < flags."""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("~/.config/meeting-assist/config.toml")

DEFAULTS: dict[str, str] = {
    "model": "claude-haiku-4-5",
    "target": "zh-TW",
    "device": "BlackHole",
    "out": "meetings",
}

KEY_ENV_VARS = {
    "assemblyai": "ASSEMBLYAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

SETTING_ENV_VARS = {name: f"MEETING_ASSIST_{name.upper()}" for name in DEFAULTS}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Keys:
    assemblyai: str | None
    anthropic: str | None
    openai: str | None


@dataclass(frozen=True)
class Settings:
    keys: Keys
    model: str
    target: str
    device: str
    out: Path


def config_path(env: Mapping[str, str]) -> Path:
    override = env.get("MEETING_ASSIST_CONFIG")
    return Path(override) if override else DEFAULT_CONFIG_PATH.expanduser()


def _read_config_file(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (keys, defaults) from the toml file; both empty when it is absent."""
    if not path.is_file():
        return {}, {}
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    for section in data:
        if section not in ("keys", "defaults"):
            log.warning("%s: unknown section [%s] ignored", path, section)
    keys = _string_table(data.get("keys", {}), KEY_ENV_VARS, path, "keys")
    defaults = _string_table(data.get("defaults", {}), DEFAULTS, path, "defaults")
    return keys, defaults


def _string_table(
    table: Any, allowed: Mapping[str, str], path: Path, section: str
) -> dict[str, str]:
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{section}] must be a table")
    out: dict[str, str] = {}
    for name, value in table.items():
        if name not in allowed:
            log.warning("%s: unknown key %s in [%s] ignored", path, name, section)
        elif not isinstance(value, str):
            raise ConfigError(f"{path}: [{section}] {name} must be a string")
        else:
            out[name] = value
    return out


def load_settings(
    overrides: Mapping[str, str | Path | None],
    *,
    env: Mapping[str, str],
    config_file: Path | None = None,
    cwd: Path | None = None,
) -> Settings:
    """Merge every configuration layer. `overrides` are command-line values;
    None entries mean "not given". `.env` in `cwd` is read but never beats
    the real environment."""
    file_keys, file_defaults = _read_config_file(config_file or config_path(env))
    dotenv_path = (cwd or Path.cwd()) / ".env"
    dotenv: dict[str, str] = {}
    if dotenv_path.is_file():
        dotenv = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}
    merged_env = {**dotenv, **env}

    def setting(name: str) -> str:
        override = overrides.get(name)
        if override is not None:
            return str(override)
        return merged_env.get(SETTING_ENV_VARS[name]) or file_defaults.get(name) or DEFAULTS[name]

    def key(name: str) -> str | None:
        return merged_env.get(KEY_ENV_VARS[name]) or file_keys.get(name) or None

    return Settings(
        keys=Keys(assemblyai=key("assemblyai"), anthropic=key("anthropic"), openai=key("openai")),
        model=setting("model"),
        target=setting("target"),
        device=setting("device"),
        out=Path(setting("out")).expanduser(),
    )
