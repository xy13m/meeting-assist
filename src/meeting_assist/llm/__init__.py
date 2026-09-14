"""One small interface over chat-completion providers.

The translator, keyterm derivation, and summarizer only ever call
`LLM.complete`. Adding a provider means one module implementing that
method and one entry in `_PREFIXES` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from meeting_assist.config import KEY_ENV_VARS, ConfigError, Keys


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


class LLM(Protocol):
    model: str

    def complete(self, system: str, messages: list[Message], max_tokens: int) -> str: ...


class LLMError(Exception):
    """A provider call failed; the message carries the provider's error."""


class UnknownModelError(Exception):
    pass


# provider name -> model-name prefixes that select it
_PREFIXES: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude",),
    "openai": ("gpt", "chatgpt", "o1", "o3", "o4"),
}


def provider_for(model: str) -> tuple[str, str]:
    """Return (provider, model name as the provider knows it)."""
    provider, sep, rest = model.partition("/")
    if sep and provider in _PREFIXES:
        return provider, rest
    lowered = model.lower()
    for name, prefixes in _PREFIXES.items():
        if lowered.startswith(prefixes):
            return name, model
    forms = ", ".join(f"{name}/<model>" for name in _PREFIXES)
    raise UnknownModelError(f"cannot tell the provider of model {model!r}; write it as {forms}")


def create_llm(model: str, keys: Keys) -> LLM:
    provider, name = provider_for(model)
    api_key: str | None = getattr(keys, provider)
    if not api_key:
        raise ConfigError(
            f"model {model!r} needs {KEY_ENV_VARS[provider]} (or [keys] {provider} in config.toml)"
        )
    if provider == "anthropic":
        from meeting_assist.llm.anthropic import AnthropicLLM

        return AnthropicLLM(api_key, name)
    from meeting_assist.llm.openai import OpenAILLM

    return OpenAILLM(api_key, name)
