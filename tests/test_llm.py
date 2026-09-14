from types import SimpleNamespace

import pytest

from meeting_assist.config import ConfigError, Keys
from meeting_assist.llm import LLMError, Message, UnknownModelError, create_llm, provider_for
from meeting_assist.llm.anthropic import AnthropicLLM
from meeting_assist.llm.openai import OpenAILLM

KEYS = Keys(assemblyai=None, anthropic="ant", openai="oai")


@pytest.mark.parametrize(
    "model, expected",
    [
        ("claude-haiku-4-5", ("anthropic", "claude-haiku-4-5")),
        ("Claude-Opus-5", ("anthropic", "Claude-Opus-5")),
        ("gpt-4.1-mini", ("openai", "gpt-4.1-mini")),
        ("o3-mini", ("openai", "o3-mini")),
        ("chatgpt-4o-latest", ("openai", "chatgpt-4o-latest")),
        ("openai/my-fine-tune", ("openai", "my-fine-tune")),
        ("anthropic/claude-custom", ("anthropic", "claude-custom")),
    ],
)
def test_provider_for(model, expected):
    assert provider_for(model) == expected


def test_provider_for_unknown_model_lists_explicit_forms():
    with pytest.raises(UnknownModelError, match="anthropic/.*openai/"):
        provider_for("mistral-large")


def test_create_llm_requires_the_provider_key():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        create_llm("claude-haiku-4-5", Keys(None, None, None))
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        create_llm("gpt-4.1-mini", Keys(None, None, None))


def test_create_llm_builds_the_matching_provider():
    llm = create_llm("claude-haiku-4-5", KEYS)
    assert isinstance(llm, AnthropicLLM)
    assert llm.model == "claude-haiku-4-5"
    llm = create_llm("openai/custom", KEYS)
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "custom"


class FakeAnthropicClient:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result

    error = None
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text=" 你好"),
            SimpleNamespace(type="tool_use"),
            SimpleNamespace(type="text", text="。 "),
        ]
    )


def test_anthropic_complete_sends_system_and_messages_and_joins_text_blocks():
    client = FakeAnthropicClient()
    llm = AnthropicLLM("key", "claude-haiku-4-5", client=client)
    out = llm.complete("SYS", [Message("user", "a"), Message("assistant", "b")], max_tokens=42)
    assert out == "你好。"
    assert client.calls == [
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 42,
            "system": "SYS",
            "messages": [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
            ],
        }
    ]


def test_anthropic_wraps_sdk_errors():
    client = FakeAnthropicClient()
    client.error = RuntimeError("rate limited")
    with pytest.raises(LLMError, match="rate limited"):
        AnthropicLLM("key", "m", client=client).complete("s", [Message("user", "a")], 10)


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []
        self.responses = self
        self.error = None

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=" hi ")


def test_openai_complete_uses_the_responses_api():
    client = FakeOpenAIClient()
    llm = OpenAILLM("key", "gpt-4.1-mini", client=client)
    out = llm.complete("SYS", [Message("user", "a"), Message("assistant", "b")], max_tokens=42)
    assert out == "hi"
    assert client.calls == [
        {
            "model": "gpt-4.1-mini",
            "instructions": "SYS",
            "input": [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
            ],
            "max_output_tokens": 42,
        }
    ]


def test_openai_wraps_sdk_errors():
    client = FakeOpenAIClient()
    client.error = RuntimeError("boom")
    with pytest.raises(LLMError, match="boom"):
        OpenAILLM("key", "m", client=client).complete("s", [Message("user", "a")], 10)


def test_real_clients_are_built_when_none_is_given():
    assert AnthropicLLM("key", "m").model == "m"
    assert OpenAILLM("key", "m").model == "m"
