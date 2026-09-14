---
name: meeting-assist
description: Follow a live meeting-assist transcript and suggest a reply after each finished turn. Use during a meeting that `meeting-assist listen` is transcribing, or against a --wav replay to test. Also handles "/meeting-assist stop".
---

# Meeting assist

Follow a running `meeting-assist listen` transcript and, each time another
participant finishes a turn, give the user a short suggestion for what to
say next. This skill only reads the files the CLI writes; it does not run
or change the CLI.

Invocation: `/meeting-assist [meeting-dir]` or `/meeting-assist stop`.

## Files the CLI writes

Each run creates one meeting directory (default `meetings/<YYYYMMDD-HHMMSS>/`
under the directory where `meeting-assist listen` was started) containing:

- `transcript.jsonl`: one JSON object per line, appended as events happen.
  Records are written with `json.dumps` defaults, so a key looks like
  `"type": "turn"` with a space after the colon.
  - `{"type": "meta", ...}`: always the first line. `target` is the
    translation target language code (for example `zh-TW`), `languages` the
    codes the recogniser was asked to listen for.
  - `{"type": "turn", ...}`: one finished turn. Fields: `turn_order` (int,
    unique within the run), `speaker` (a provisional label such as `A`),
    `text` (what was said, in the language spoken), `started_at` and
    `ended_at` (ISO timestamps), `language` (the recogniser's guess, or
    null).
  - `{"type": "translation", ...}`: the translation of a turn, about a
    second after the turn.
  - `{"type": "revision", ...}`: a corrected speaker label for an earlier
    turn; these arrive at the end of the session.
- `transcript.md`: readable transcript, rewritten with revised labels on exit.
- `meeting.md`: the context file the user passed with `--context`, if any.
- `meeting-assist.log`: warnings from the run.

## Start

1. Resolve the meeting directory. Use the argument if one was given.
   Otherwise take the newest directory under `meetings/`:
   `ls -td meetings/*/ | head -n 1`. If there is none, or it has no
   `transcript.jsonl`, say so and stop.
2. Read `meeting.md` in that directory if it exists. It is the context the
   user wrote before the meeting: agenda, participants, product names,
   terms. If it is missing, say so in one line and carry on.
3. Read the `meta` line to learn the target language:
   `head -n 1 <dir>/transcript.jsonl`. The summary line of every
   suggestion is written in that language.
4. Read the last 20 turns already recorded, if any, so you know where the
   conversation is:
   `grep '"type": "turn"' <dir>/transcript.jsonl | tail -n 20`
5. Unless `meeting.md` already says so, ask the user one question: who are
   they in this meeting (name and role)? Every suggestion is written from
   that person's position.
6. Arm the watch with the Monitor tool, `persistent: true`, description
   `meeting-assist turns in <dir>`:

   ```
   tail -n 0 -F <dir>/transcript.jsonl | grep --line-buffered '"type": "turn"'
   ```

   Each finished turn is one event. Translation and revision records are
   filtered out on purpose: the original text is enough, and it arrives
   about a second before the translation.
7. Tell the user the watch is armed and that suggestions arrive roughly ten
   seconds after a speaker stops (transcription plus your own turn).

## On each turn event

The event is one `turn` line. Work from that line alone; do not re-read
the file. `text` is in whatever language was spoken; when the target
language is Traditional Chinese, Chinese text is already in Traditional
characters. Ignore `language`: it is the recogniser's guess and it labels
plain English turns `"zh"` often enough to mislead; judge the language
from `text`.

First decide whether the turn is complete:

- **Complete**: `text` ends with `.`, `?`, `!`, `。`, `？` or `！`, or the
  speaker differs from the previous event's speaker.
- **Fragment**: anything else. The recogniser cuts turns at about ten
  seconds, so one thought often arrives as several lines.

For a fragment, reply with exactly one line and nothing else:

```
… (<speaker> continuing)
```

Keep its text in mind; it belongs to the next complete turn from the same
speaker.

For a complete turn, join it with any fragments that preceded it from the
same speaker, then reply in exactly this shape:

```
**<speaker>**: <one line in the target language: what they said or asked, as it matters to the user>
**Reply**: "<one or two sentences the user can say as-is, in the language the turn was spoken in>"
```

Rules for the suggestion:

- Write from the user's position. Use `meeting.md` for names, facts, and
  terms; do not invent facts that are not in it or in the transcript.
- If the turn needs no reply from the user (small talk, one participant
  answering another, a statement with no question in it), replace the
  second line with `**No reply needed**` plus a few words on why.
- If a good reply depends on something you do not know, say what the user
  should ask instead of guessing. A one-line `**Ask**: "…"` is fine.
- Answer in the language the turn was spoken in: an English suggestion for
  an English turn, a Chinese suggestion for a Chinese turn.
- Speaker labels are provisional until the meeting ends. Never say which
  label is which person unless the user told you.
- Keep every reply this short. Hundreds of events arrive per hour and each
  word stays in context.
- The user cannot be heard: the CLI records system audio only. Do not
  assume they have or have not already answered.

## After context compaction

Before answering the next event, re-read `meeting.md`, the `meta` line,
and the last 20 turn lines (steps 2 to 4). Do not arm a second monitor;
the existing one keeps running.

## Stop

On `/meeting-assist stop`, or when the user asks in words, stop the monitor
with TaskStop and confirm in one line. When the CLI exits the file goes
quiet, but the watch stays armed until it is stopped.
