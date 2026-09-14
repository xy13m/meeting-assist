"""Target-language policy.

The pipeline asks the policy two things: how to normalise recognised text
before anyone sees it, and whether a turn is already in the target language
and can skip translation. Only Chinese targets answer either question with
anything but "leave it alone" / "no"; the decision is always made from the
text, never from the recogniser's per-turn language label, because that
label mislabels plain English turns as "zh" too often to trust.
"""

from __future__ import annotations

from typing import Protocol

_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "zh-TW": "Traditional Chinese (Taiwan)",
    "zh-CN": "Simplified Chinese",
}


def display_name(code: str) -> str:
    return _NAMES.get(code, code)


class TargetLanguage(Protocol):
    code: str
    name: str

    def normalize(self, text: str) -> str: ...

    def is_already_target(self, text: str) -> bool: ...


class GenericTarget:
    """Any language without special handling: never skips translation."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.name = display_name(code)

    def normalize(self, text: str) -> str:
        return text

    def is_already_target(self, text: str) -> bool:
        return False


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0x3400 <= code <= 0x4DBF  # Extension A
        or 0xF900 <= code <= 0xFAFF  # Compatibility Ideographs
    )


def has_chinese(text: str) -> bool:
    return any(_is_cjk(ch) for ch in text)


def is_mostly_chinese(text: str) -> bool:
    """True when Chinese characters carry the sentence. One Chinese character
    says about as much as two Latin letters, so a Chinese sentence with a few
    English terms in it still counts as Chinese, while an English sentence
    with one Chinese word does not."""
    cjk = sum(1 for ch in text if _is_cjk(ch))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return cjk > 0 and cjk * 2 >= latin


class ChineseTarget:
    """zh-TW converts Simplified to Traditional (Taiwan vocabulary); zh-CN
    keeps the recogniser's Simplified output. Both skip translation for
    turns spoken mostly in Chinese."""

    _converter = None

    def __init__(self, code: str) -> None:
        self.code = code
        self.name = display_name(code)

    def normalize(self, text: str) -> str:
        if self.code != "zh-TW" or not has_chinese(text):
            return text
        if ChineseTarget._converter is None:
            from opencc import OpenCC

            ChineseTarget._converter = OpenCC("s2twp")
        converted: str = ChineseTarget._converter.convert(text)
        return converted

    def is_already_target(self, text: str) -> bool:
        return is_mostly_chinese(text)


def for_code(code: str) -> TargetLanguage:
    lowered = code.strip().lower()
    if lowered in ("zh", "zh-tw", "zh-hant"):
        return ChineseTarget("zh-TW")
    if lowered in ("zh-cn", "zh-hans"):
        return ChineseTarget("zh-CN")
    return GenericTarget(code.strip())
