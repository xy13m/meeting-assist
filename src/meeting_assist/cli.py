"""Command-line entry point: listen, summarize, install-skill."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import wave
from collections.abc import Sequence
from pathlib import Path

from meeting_assist import __version__
from meeting_assist.audio import AudioSource, DeviceNotFound, DeviceSource, FileSource
from meeting_assist.config import ConfigError, Settings, load_settings
from meeting_assist.context import MeetingContext, derive_keyterms, load_context
from meeting_assist.language import for_code
from meeting_assist.llm import LLM, LLMError, UnknownModelError, create_llm
from meeting_assist.logging_setup import file_logging
from meeting_assist.pipeline import build_pipeline
from meeting_assist.skill import SKILL_NAME, skill_path
from meeting_assist.store import Store
from meeting_assist.stt import DEFAULT_LANGUAGES, create_transcriber
from meeting_assist.summarizer import EmptyTranscript, summarize

log = logging.getLogger(__name__)


class UsageError(Exception):
    """A problem the user can fix; printed as one line, exit code 1."""


def parse_languages(value: str) -> tuple[str, ...]:
    codes = tuple(code.strip() for code in value.split(",") if code.strip())
    if not codes:
        raise argparse.ArgumentTypeError("expected one or more comma-separated language codes")
    return codes


def _add_model_and_target(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", help="LLM model, e.g. claude-haiku-4-5 or gpt-4.1-mini")
    p.add_argument("--target", metavar="CODE", help="translation target language, e.g. zh-TW")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meeting-assist",
        description="Live meeting transcription and translation in the terminal.",
    )
    p.add_argument("--version", action="version", version=f"meeting-assist {__version__}")
    sub = p.add_subparsers(dest="command")

    listen = sub.add_parser("listen", help="transcribe and translate a live meeting")
    listen.add_argument("--context", type=Path, help="meeting context Markdown file")
    listen.add_argument("--device", help="input device name substring (default: BlackHole)")
    listen.add_argument("--wav", type=Path, help="replay a mono 16-bit WAV instead of a device")
    listen.add_argument("--out", type=Path, help="directory for meeting folders")
    _add_model_and_target(listen)
    listen.add_argument("--no-keyterms", action="store_true", help="skip keyterm derivation")
    listen.add_argument(
        "--languages",
        type=parse_languages,
        default=DEFAULT_LANGUAGES,
        metavar="CODES",
        help="comma-separated language codes to recognise, e.g. en,zh (default: en)",
    )

    summ = sub.add_parser("summarize", help="write summary.md for a recorded meeting")
    summ.add_argument("meeting_dir", type=Path, help="meeting directory written by listen")
    _add_model_and_target(summ)
    summ.add_argument("--force", action="store_true", help="overwrite an existing summary.md")

    skill = sub.add_parser("install-skill", help="install the Claude Code skill")
    skill.add_argument("--dest", type=Path, help="skills directory (default: ~/.claude/skills)")
    skill.add_argument("--force", action="store_true", help="overwrite an installed skill")
    return p


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


# -- shared helpers ------------------------------------------------------------


def _settings(args: argparse.Namespace) -> Settings:
    overrides = {name: getattr(args, name, None) for name in ("model", "target", "device", "out")}
    try:
        return load_settings(overrides, env=os.environ)
    except ConfigError as exc:
        raise UsageError(str(exc)) from exc


def _llm(settings: Settings) -> LLM:
    try:
        return create_llm(settings.model, settings.keys)
    except (ConfigError, UnknownModelError) as exc:
        raise UsageError(str(exc)) from exc


# -- listen --------------------------------------------------------------------


def _open_source(args: argparse.Namespace, settings: Settings) -> AudioSource:
    try:
        if args.wav:
            return FileSource(args.wav)
        return DeviceSource(settings.device)
    except (DeviceNotFound, ValueError, OSError, wave.Error) as exc:
        name = args.wav or settings.device
        raise UsageError(f"cannot open audio source {name}: {exc}") from exc


def run_listen(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if not settings.keys.assemblyai:
        raise UsageError("ASSEMBLYAI_API_KEY is not set (or [keys] assemblyai in config.toml)")
    llm = _llm(settings)
    target = for_code(settings.target)

    context = MeetingContext.empty()
    if args.context is not None:
        try:
            context = load_context(args.context)
        except OSError as exc:
            raise UsageError(f"cannot read context file {args.context}: {exc}") from exc

    source = _open_source(args, settings)
    store = Store(
        settings.out, target=target.code, languages=args.languages, context_path=args.context
    )
    with file_logging(store.meeting_dir / "meeting-assist.log"):
        keyterms = context.keyterms
        if not keyterms and not args.no_keyterms and context.text.strip():
            keyterms = derive_keyterms(context.text, llm)
        print(f"Keyterms: {len(keyterms)}", file=sys.stderr)

        pipeline = build_pipeline(
            source=source,
            llm=llm,
            context=context,
            keyterms=keyterms,
            store=store,
            target=target,
            languages=args.languages,
            api_key=settings.keys.assemblyai,
            transcriber_factory=create_transcriber,
        )
        print(f"Writing to {store.meeting_dir}  (Ctrl-C to stop)", file=sys.stderr)
        pipeline.run()

    reason = pipeline.transcriber.abort_reason
    if reason:
        print(f"Stopped early: {reason}", file=sys.stderr)
    print(f"Transcript saved in {store.meeting_dir}", file=sys.stderr)
    return 2 if reason else 0


# -- summarize -----------------------------------------------------------------


def run_summarize(args: argparse.Namespace) -> int:
    meeting_dir: Path = args.meeting_dir
    if not (meeting_dir / "transcript.jsonl").is_file():
        raise UsageError(f"no transcript.jsonl in {meeting_dir}")
    out_path = meeting_dir / "summary.md"
    if out_path.exists() and not args.force:
        raise UsageError(f"{out_path} already exists; pass --force to overwrite it")
    settings = _settings(args)
    llm = _llm(settings)
    try:
        text = summarize(meeting_dir, llm, for_code(settings.target))
    except EmptyTranscript as exc:
        raise UsageError(str(exc)) from exc
    except LLMError as exc:
        raise UsageError(f"summary request failed: {exc}") from exc
    out_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"Summary written to {out_path}", file=sys.stderr)
    return 0


# -- install-skill -------------------------------------------------------------

DEFAULT_SKILLS_DIR = Path("~/.claude/skills")


def run_install_skill(args: argparse.Namespace) -> int:
    dest_dir: Path = (args.dest or DEFAULT_SKILLS_DIR).expanduser() / SKILL_NAME
    target = dest_dir / "SKILL.md"
    if target.exists() and not args.force:
        raise UsageError(f"{target} already exists; pass --force to overwrite it")
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_path(), target)
    print(f"Skill installed to {target}", file=sys.stderr)
    return 0


# -- entry point ---------------------------------------------------------------

COMMANDS = {"listen": run_listen, "summarize": run_summarize, "install-skill": run_install_skill}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 1
    try:
        return COMMANDS[args.command](args)
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
