import logging
from pathlib import Path

from meeting_assist.context import (
    MeetingContext,
    clean_keyterms,
    derive_keyterms,
    load_context,
    parse_keyterm_lines,
    parse_keyterms_section,
    summary_prompt,
)
from tests.conftest import FakeLLM

SAMPLE = """# Q3 roadmap review

Participants: Alice Chen (PM), Bob Lee (Eng lead)

## Agenda
- Launch date for Sentinel
- Budget

## Keyterms
- Sentinel
- Alice Chen
- Bob Lee
-   LiteLLM proxy
"""


def test_parse_keyterms_section_returns_bullets():
    assert parse_keyterms_section(SAMPLE) == ["Sentinel", "Alice Chen", "Bob Lee", "LiteLLM proxy"]


def test_parse_keyterms_section_absent_returns_none():
    assert parse_keyterms_section("# Nothing\n\n- not a keyterm\n") is None


def test_parse_keyterms_section_stops_at_next_heading():
    text = "## Keyterms\n- One\n- Two\n\n## Notes\n- Three\n"
    assert parse_keyterms_section(text) == ["One", "Two"]


def test_summary_prompt_uses_heading_or_first_200_chars():
    assert summary_prompt(SAMPLE) == "Q3 roadmap review"
    assert summary_prompt("x" * 500) == "x" * 200
    assert summary_prompt("") == ""


def test_clean_keyterms_limits_and_dedupes():
    terms = ["  Alice ", "alice", "", "y" * 50, "ok"] + [f"t{i}" for i in range(200)]
    cleaned = clean_keyterms(terms)
    assert cleaned[:2] == ["Alice", "ok"]
    assert len(cleaned) == 100
    assert "y" * 50 not in cleaned


def test_load_context(tmp_path: Path):
    p = tmp_path / "meeting.md"
    p.write_text(SAMPLE)
    ctx = load_context(p)
    assert isinstance(ctx, MeetingContext)
    assert ctx.text == SAMPLE
    assert ctx.keyterms == ["Sentinel", "Alice Chen", "Bob Lee", "LiteLLM proxy"]
    assert ctx.prompt == "Q3 roadmap review"


def test_load_context_without_section_has_no_keyterms(tmp_path: Path):
    p = tmp_path / "meeting.md"
    p.write_text("# Sync\nJust notes.\n")
    assert load_context(p).keyterms == []


def test_empty_context_constant():
    assert MeetingContext.empty() == MeetingContext(text="", keyterms=[], prompt="")


def test_parse_keyterm_lines_strips_bullets_and_numbering():
    text = "- Sentinel\n* Alice Chen\n3. Zeta\n\n  Bob Lee  \nKeyterms:\n"
    assert parse_keyterm_lines(text) == ["Sentinel", "Alice Chen", "Zeta", "Bob Lee", "Keyterms:"]


def test_derive_keyterms_asks_for_one_term_per_line_and_cleans():
    llm = FakeLLM(["- Sentinel\nsentinel\nBob Lee\n"])
    terms = derive_keyterms("# Sync\nSentinel launch with Bob Lee", llm)
    assert terms == ["Sentinel", "Bob Lee"]
    system, messages, max_tokens = llm.calls[0]
    assert "speech recogniser" in system
    assert "one term per line" in system
    assert max_tokens == 2000
    assert messages[0].role == "user"
    assert "Sentinel launch" in messages[0].content


def test_derive_keyterms_returns_empty_on_error(caplog):
    llm = FakeLLM([RuntimeError("api down")])
    with caplog.at_level(logging.WARNING):
        assert derive_keyterms("# Sync", llm) == []
    assert "api down" in caplog.text
