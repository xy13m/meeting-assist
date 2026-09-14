"""OpenAI Responses API behind the LLM interface."""

from __future__ import annotations

from typing import Any

import openai

from meeting_assist.llm import LLMError, Message


class OpenAILLM:
    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self.model = model
        self._client: Any = client or openai.OpenAI(api_key=api_key)

    def complete(self, system: str, messages: list[Message], max_tokens: int) -> str:
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=system,
                input=[{"role": m.role, "content": m.content} for m in messages],
                max_output_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        text: str = response.output_text
        return text.strip()
