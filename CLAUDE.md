# CLAUDE.md

Invariants for changing meeting-assist. README.md explains setup and use.

## Layout

```
src/meeting_assist/
  cli.py          listen / summarize / install-skill
  config.py       defaults < config.toml < .env < environment < flags
  events.py       the only types stages exchange
  pipeline.py     routes events; depends on protocols only
  audio.py        FileSource, DeviceSource (sounddevice, imported lazily)
  stt/            Transcriber protocol; assemblyai.py is the only SDK importer
  llm/            LLM protocol; anthropic.py and openai.py are the only SDK importers
  language.py     TargetLanguage policy; the only importer of opencc
  transcript.py   jsonl record shapes, reader, Markdown block
  store.py        per-meeting files
  translator.py   ordered single worker
  summarizer.py   summary.md
  display.py      rich terminal output
  skill/SKILL.md  the Claude Code skill shipped in the wheel
```

`.claude/skills/meeting-assist` is a symlink to `src/meeting_assist/skill`.
Edit the skill there only.

## Boundaries

- `tests/test_boundaries.py` fails if `assemblyai`, `anthropic`, `openai`,
  `opencc`, or `sounddevice` is imported anywhere but its boundary module.
- The pipeline talks to `stt.Transcriber`, `llm.LLM`, and
  `language.TargetLanguage`. Adding a provider means one module in `llm/`
  plus one entry in `_PREFIXES` in `llm/__init__.py`; the pipeline does not
  change. Adding a target language with special handling means one class in
  `language.py` and a case in `for_code`.
- Only tests under `tests/stt/` may import AssemblyAI types. Every other
  test uses `FakeLLM`, the fakes in `tests/stt/fakes.py`, or our own events.

## Behaviour that must survive

- Audio chunks are mono int16 PCM, 50 ms. `sample_rate` is whatever the
  source reports and is passed to AssemblyAI unchanged; nothing resamples.
- A `FinalTurn` is emitted exactly once per turn order, from the formatted
  end-of-turn message. The unformatted end-of-turn is held and emitted, with
  a warning in the log, only if no formatted version arrived by the time a
  later turn finalises, a reconnect happens, or the session ends.
- Reconnects (on an error, a server-initiated TerminationEvent, or a silent
  websocket close detected through the SDK's private `_stop_event`) share
  one consecutive-failure budget and offset turn orders so they stay unique
  within a run. A successful Begin resets the budget.
- Turn orders are unique within a run for one AssemblyAI session. A second
  session (a microphone, say) must namespace its orders before sharing
  Store or Display.
- Speaker revisions arrive at session end. `transcript.md` is appended live
  and rewritten with revised labels on close through a temp file, fsync,
  and rename; `transcript.jsonl` keeps the raw sequence including revision
  records.
- The translator is one ordered worker: output order must match speech
  order. Do not parallelise it without adding reordering.
- Whether a turn skips translation is decided by
  `TargetLanguage.is_already_target(text)`, never from AssemblyAI's per-turn
  `language_code`: that label marked plain English turns as `zh` in live
  tests. Only Chinese targets ever skip; `zh-TW` also converts Simplified to
  Traditional before any stage sees the text.
- Keyterm derivation runs before the AssemblyAI session opens, so no
  billed time is spent waiting on the LLM. AssemblyAI bills connection
  time: never leave a session open while idle.
- `listen` opens the audio source before it creates the meeting directory,
  so a missing device leaves nothing behind.
- `summarize` never runs on its own at the end of `listen`.
- Unit tests must not touch the network. Anything that does is marked
  `integration` and excluded by default.
- The default model is `claude-haiku-4-5`, chosen for latency. Changing a
  default is a product decision, not a refactor.

## transcript.jsonl: the contract with the skill

`skill/SKILL.md` greps the file for the literal `"type": "turn"` and reads
the fields of those lines, and reads the first line for `target`. Records
are written with `json.dumps(record, ensure_ascii=False)` and its default
separators, which is what puts the space after the colon. Do not change the
separators. Changing any shape below means updating SKILL.md, this file,
and `tests/test_skill.py`.

```
{"type": "meta", "version": 1, "started_at": ISO, "target": "zh-TW", "languages": ["en"]}
{"type": "turn", "turn_order": int, "speaker": str, "text": str, "started_at": ISO, "ended_at": ISO, "language": str | null}
{"type": "translation", "turn_order": int, "text": str, "error": str | null}
{"type": "revision", "turn_order": int, "speaker": str}
```

`meta` is always the first line. A `revision` may precede the `turn` it
refers to when the turn was held (unformatted fallback) until shutdown.

## Packaging

- The sdist target uses `only-include`, not `include`: walking the
  `.claude/skills/meeting-assist` symlink made hatchling drop the real
  `skill/` directory. CI checks that the wheel contains
  `meeting_assist/skill/SKILL.md`.

## Checks

```
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv build
```
