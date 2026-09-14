"""Vendor SDKs stay behind their boundary modules. Enforced by scanning imports."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "meeting_assist"

ALLOWED = {
    "assemblyai": {"stt/assemblyai.py"},
    "anthropic": {"llm/anthropic.py"},
    "openai": {"llm/openai.py"},
    "opencc": {"language.py"},
    "sounddevice": {"audio.py"},
}


def importers(module: str) -> set[str]:
    pattern = re.compile(rf"^\s*(?:import {module}\b|from {module}\b)", re.MULTILINE)
    found = set()
    for path in SRC.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(SRC).as_posix())
    return found


def test_each_sdk_is_imported_only_by_its_boundary_module():
    for module, allowed in ALLOWED.items():
        assert importers(module) == allowed, module


def test_pipeline_and_cli_do_not_import_vendor_modules():
    for name in ("pipeline.py", "cli.py", "translator.py", "summarizer.py", "context.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "meeting_assist.stt.assemblyai" not in text, name
        assert "meeting_assist.llm.anthropic" not in text, name
        assert "meeting_assist.llm.openai" not in text, name
