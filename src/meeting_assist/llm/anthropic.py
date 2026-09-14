"""Anthropic Messages API behind the LLM interface."""

from __future__ import annotations

from typing import Any

import anthropic

from meeting_assist.llm import LLMError, Message


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self.model = model
        self._client: Any = client or anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, messages: list[Message], max_tokens: int) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        parts = [b.text for b in response.content if b.type == "text"]
        return "".join(parts).strip()
