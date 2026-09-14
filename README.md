# meeting-assist

Live meeting transcription and translation in the terminal, with a Claude
Code skill that suggests what to say next.

`meeting-assist listen` captures the audio of a meeting playing on your Mac,
streams it to AssemblyAI for transcription with speaker labels, translates
each finished turn with Claude or ChatGPT, shows both in the terminal, and
saves a transcript. `meeting-assist summarize` writes minutes afterwards.
The bundled Claude Code skill follows the live transcript and suggests a
reply after each turn.

## Requirements

- macOS. Audio capture uses a macOS virtual audio device; there is no
  Linux or Windows support.
- Python 3.14.
- An [AssemblyAI](https://www.assemblyai.com/) API key.
- An [Anthropic](https://console.anthropic.com/) or
  [OpenAI](https://platform.openai.com/) API key, depending on the model you
  pick.
- [BlackHole](https://existential.audio/blackhole/) 2ch:
  `brew install --cask blackhole-2ch`.

## Install

```
uv tool install meeting-assist
```

or `pipx install meeting-assist`. To work on the source instead:

```
git clone https://github.com/xy13m/meeting-assist
cd meeting-assist
uv sync --all-groups
uv run meeting-assist --help
```

## One-time audio setup

The tool reads whatever is played into BlackHole. Route the meeting audio
there while still hearing it yourself:

1. Open Audio MIDI Setup (Applications > Utilities).
2. Click `+` at the bottom left and choose "Create Multi-Output Device".
3. Tick your real output (speakers or headphones) and "BlackHole 2ch".
   Tick "Drift Correction" for BlackHole.
4. Either set this Multi-Output Device as the system output (menu bar sound
   icon), or pick it as the speaker in your meeting app's audio settings.

While a Multi-Output Device is the system output, the keyboard volume keys
do nothing. Selecting it only inside the meeting app avoids that.

The microphone is not captured; your own speech does not appear.

## Configuration

Settings come from four places. Later ones override earlier ones.

1. `~/.config/meeting-assist/config.toml`:

   ```toml
   [keys]
   assemblyai = "..."
   anthropic = "..."
   openai = "..."

   [defaults]
   model = "claude-haiku-4-5"
   target = "zh-TW"
   device = "BlackHole"
   out = "~/meetings"
   ```

2. A `.env` file in the current directory (keys only, same variable names as
   below).
3. Environment variables: `ASSEMBLYAI_API_KEY`, `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, `MEETING_ASSIST_MODEL`, `MEETING_ASSIST_TARGET`,
   `MEETING_ASSIST_DEVICE`, `MEETING_ASSIST_OUT`.
4. Command-line flags: `--model`, `--target`, `--device`, `--out`.

Built-in defaults: model `claude-haiku-4-5`, target `zh-TW`, device
`BlackHole`, output directory `meetings/` under the current directory.
`MEETING_ASSIST_CONFIG` points at a different config file.

The provider is chosen from the model name: `claude-*` uses Anthropic;
`gpt-*`, `chatgpt-*` and the `o1`/`o3`/`o4` series use OpenAI. For any other
name write `anthropic/<model>` or `openai/<model>`.

The target language takes an IETF-style code such as `zh-TW`, `ja`, or `en`.
When the target is Traditional Chinese, Mandarin recognised in Simplified
characters is converted to Traditional (Taiwan vocabulary), and turns spoken
mostly in Chinese are not sent to the translator. Other targets translate
every turn.

## Listen

```
meeting-assist listen
meeting-assist listen --context meeting.md
meeting-assist listen --context meeting.md --languages en,zh --target zh-TW
```

Write a context file before the meeting: agenda, participants, product
names, acronyms, anything that helps. The whole file goes into the
translator's system prompt, and its first heading is passed to the
recogniser as a hint. An optional `## Keyterms` bullet list is sent to the
recogniser as-is; without one, the LLM derives up to 100 keyterms from the
text (skip that with `--no-keyterms`). Without `--context` there are no
keyterms and the translator works from the transcript alone.

Options:

| Flag | Meaning |
|---|---|
| `--context FILE` | Meeting context Markdown file. Copied into the meeting directory as `meeting.md`. |
| `--languages CODES` | Comma-separated AssemblyAI language codes to recognise. Default `en`. |
| `--wav FILE` | Replay a mono 16-bit WAV instead of a device, at real-time pace. |
| `--device NAME` | Input device name substring. |
| `--out DIR` | Directory that meeting folders are created in. |
| `--model NAME` | LLM for translation and keyterm derivation. |
| `--target CODE` | Translation target language. |
| `--no-keyterms` | Do not derive keyterms from the context file. |

Recognition is English-only by default. For a meeting where people also
speak Mandarin, pass `--languages en,zh`: the recogniser then switches
between the two, even inside one sentence. A single code forces a
monolingual session, which is why the default is `en` alone. Whether a turn
skips translation is decided from its text: the recogniser's per-turn
language label is stored but not trusted, because in testing it marked
plain English turns as `zh`.

Speaker labels shown live are provisional. AssemblyAI sends corrected
labels when the session ends; `transcript.md` is rewritten with them on
exit, and `transcript.jsonl` keeps the corrections as `revision` records.

Press Ctrl-C to stop. The exit code is 0 normally, 1 for a configuration or
setup problem, and 2 when the connection to AssemblyAI could not be
recovered (the transcript so far is still saved).

### Output files

Each run creates `<out>/<YYYYMMDD-HHMMSS>/` containing:

- `transcript.jsonl`: one record per line, flushed immediately. The first
  line is a `meta` record with the target language and recognised
  languages; then `turn`, `translation`, and `revision` records in the order
  they happened. See CLAUDE.md for the exact shapes.
- `transcript.md`: readable transcript, one block per turn with its
  translation.
- `meeting.md`: a copy of the context file, when one was given.
- `meeting-assist.log`: warnings from the run (reconnects, turns whose
  formatted text never arrived, translation failures).

## Summarize

```
meeting-assist summarize meetings/20260915-100000
```

Reads `transcript.jsonl` and `meeting.md`, asks the LLM for minutes, and
writes `summary.md` with a `## Summary` section and an `## Action items`
section in the target language. It only runs when you ask; `listen` never
calls it. Pass `--force` to replace an existing `summary.md`, `--model` to
use a different model than the default, `--target` for a different
language.

## Reply suggestions with Claude Code

The package ships a Claude Code skill. Install it once:

```
meeting-assist install-skill
```

This copies `SKILL.md` to `~/.claude/skills/meeting-assist/`. Pass
`--dest .claude/skills` to install it into one project instead, and
`--force` to replace an installed copy.

During a meeting, start Claude Code in the directory where you run
`meeting-assist listen` (so `meetings/` is next to it) and run
`/meeting-assist`. It picks the newest meeting directory, or pass one. It
reads `meeting.md` for context, watches `transcript.jsonl`, and prints a
one-line summary plus a suggested reply for each complete turn. Fragments
of a turn still in progress get a one-line placeholder. `/meeting-assist
stop` ends the watch.

Every turn wakes Claude Code once, so an hour-long meeting is a few hundred
requests against your Claude Code quota. Suggestions appear about ten
seconds after the speaker stops. The skill cannot hear you: the CLI records
system audio only.

## Cost

AssemblyAI bills connection time: about $0.52 per hour with speaker labels
and keyterms at the time of writing. Translation with Claude Haiku is
roughly $0.3 to $0.6 per hour. Start the tool when the meeting starts and
stop it when it ends.

## Roadmap

Not in this version:

- Automatic context generation from calendars, Confluence, Jira, or local
  folders. Write `meeting.md` by hand.
- A web UI.
- Reply suggestions inside the CLI. They stay in the Claude Code skill.
- Audio capture on Linux or Windows.

## Development

```
uv sync --all-groups
uv run pytest                 # unit tests, no network
uv run pytest -m integration  # one real run on a generated WAV; needs both keys
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv build
```

CLAUDE.md lists the behaviours a change must not break.

## License

MIT.
