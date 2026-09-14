"""The Claude Code skill shipped with the package."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

SKILL_NAME = "meeting-assist"


def skill_path() -> Path:
    return Path(str(files(__name__).joinpath("SKILL.md")))
