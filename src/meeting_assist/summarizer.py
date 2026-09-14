"""Post-meeting summary from transcript.jsonl and the context file.

Manual only: `meeting-assist summarize` never runs on its own at the end
of `listen`.
"""

from __future__ import annotations

from pathlib import Path

from meeting_assist import transcript
from meeting_assist.language import TargetLanguage
from meeting_assist.llm import LLM, Message

MAX_TOKENS = 4000

_SYSTEM = (
    "You write minutes for a business meeting from a speech-recognition "
    "transcript. Speaker labels are letters, not names; use names only when the "
    "meeting context makes the mapping clear. Do not invent facts that are not "
    "in the transcript or the context.\n\n"
    "Output Markdown with exactly two sections and nothing else:\n\n"
    "## Summary\n"
    "A few short paragraphs or bullets covering what was discussed and decided.\n\n"
    "## Action items\n"
    "A bullet list; each item names the owner when known, and the deadline if "
    "one was mentioned. Write '- none' if there are no action items.\n\n"
    "Keep the two headings in English exactly as shown. Write everything else "
    "in {target}."
)


class EmptyTranscript(Exception):
    pass


def build_prompt(
    entries: list[transcript.Entry], context_text: str, target: TargetLanguage
) -> tuple[str, str]:
    lines = [
        f"[{e.turn.started_at.strftime('%H:%M:%S')}] {e.turn.speaker}: {e.turn.text}"
        for e in entries
    ]
    context = context_text.strip() or "(not provided)"
    user = f"## Meeting context\n{context}\n\n## Transcript\n" + "\n".join(lines)
    return _SYSTEM.format(target=target.name), user


def summarize(meeting_dir: Path, llm: LLM, target: TargetLanguage) -> str:
    """Return the summary Markdown for the meeting in `meeting_dir`."""
    _, entries = transcript.assemble(transcript.read_records(meeting_dir / "transcript.jsonl"))
    if not entries:
        raise EmptyTranscript(f"no turns in {meeting_dir / 'transcript.jsonl'}")
    context_path = meeting_dir / "meeting.md"
    context_text = context_path.read_text(encoding="utf-8") if context_path.is_file() else ""
    system, user = build_prompt(entries, context_text, target)
    return llm.complete(system, [Message("user", user)], max_tokens=MAX_TOKENS)
