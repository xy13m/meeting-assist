"""Plain dataclasses passed between the pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PartialTurn:
    """An in-progress turn; text grows until the turn is finalised."""

    turn_order: int
    speaker: str
    text: str


@dataclass(frozen=True)
class FinalTurn:
    """A finished, formatted turn. Emitted exactly once per turn_order."""

    turn_order: int
    speaker: str
    text: str
    started_at: datetime
    ended_at: datetime
    # ISO 639-1 code reported by the recogniser when language detection is
    # on (e.g. "en", "zh"); None in single-language sessions. Informational
    # only: the recogniser mislabels turns too often for it to drive logic.
    language: str | None = None


@dataclass(frozen=True)
class Translation:
    """Translation of one FinalTurn. text is empty when error is set."""

    turn_order: int
    text: str
    error: str | None = None


@dataclass(frozen=True)
class SpeakerRevision:
    """A late speaker-label correction for an already emitted turn."""

    turn_order: int
    speaker: str


Event = PartialTurn | FinalTurn | SpeakerRevision
