"""The packaged skill and its contract with the jsonl records."""

from pathlib import Path

from meeting_assist import transcript
from meeting_assist.events import FinalTurn
from meeting_assist.skill import skill_path
from tests.conftest import T0

REPO = Path(__file__).resolve().parents[1]


def test_packaged_skill_is_the_repo_skill():
    packaged = skill_path()
    assert packaged.is_file()
    repo_link = REPO / ".claude" / "skills" / "meeting-assist"
    assert repo_link.is_symlink()
    assert (repo_link / "SKILL.md").resolve() == packaged.resolve()


def test_skill_documents_every_turn_field_and_the_grep_literal():
    text = skill_path().read_text(encoding="utf-8")
    assert text.startswith("---\nname: meeting-assist\n")
    for field in transcript.turn_record(FinalTurn(0, "A", "x", T0, T0)):
        if field != "type":
            assert f"`{field}`" in text, field
    assert '"type": "turn"' in text
    assert '"type": "meta"' in text
    assert "meeting-assist listen" in text
    assert "rtst" not in text
