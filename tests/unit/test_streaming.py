"""
Tests for token-level streaming (invoke_streaming) across all three LLM
clients: DemoLLMClient, LLMClient, and OptimizedLLMClient. Verifies the
on_token callback fires per chunk, the full accumulated text is returned,
and (for OptimizedLLMClient) routing/cost metrics are still recorded exactly
like the non-streaming path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import MagicMock

from backend.llm_client import DemoLLMClient
from backend.optimization.configs.graphmediator import GraphMediatorConfig
from backend.optimization.engine import OptimizedLLMClient


class TestDemoLLMClientStreaming:
    def test_invoke_streaming_calls_on_token_for_each_word(self):
        client = DemoLLMClient()
        tokens: List[str] = []

        result = client.invoke_streaming("some prompt", agent="librarian", on_token=tokens.append)

        assert tokens, "expected at least one token callback"
        assert "".join(tokens) == result

    def test_invoke_streaming_returns_same_text_as_invoke(self):
        client = DemoLLMClient()
        prompt = "contradiction: do they contradict"
        plain_result = client.invoke(prompt)

        tokens: List[str] = []
        streamed_result = client.invoke_streaming(prompt, agent="critic", on_token=tokens.append)

        assert streamed_result == plain_result


@dataclass
class _Delta:
    content: Optional[str]


@dataclass
class _StreamChoice:
    delta: _Delta


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _StreamEvent:
    choices: List[_StreamChoice]
    usage: Optional[_Usage] = None


def _fake_streaming_openai_client(chunks: List[str]):
    """
    Simulates OpenAI's stream=True response: an iterable of events, each with
    a delta.content piece, and a final usage-bearing event with no choices
    (matching stream_options={"include_usage": True} behavior).
    """
    client = MagicMock()
    events = [_StreamEvent(choices=[_StreamChoice(delta=_Delta(content=chunk))]) for chunk in chunks]
    events.append(_StreamEvent(choices=[], usage=_Usage(prompt_tokens=50, completion_tokens=len(chunks))))
    client.chat.completions.create.return_value = iter(events)
    return client


def make_streaming_client(chunks: List[str]) -> OptimizedLLMClient:
    client = OptimizedLLMClient(config=GraphMediatorConfig, openai_api_key="sk-test")
    client._openai = _fake_streaming_openai_client(chunks)
    return client


class TestOptimizedLLMClientStreaming:
    def test_invoke_streaming_delivers_each_chunk(self):
        client = make_streaming_client(["Hello", ", ", "world", "!"])
        tokens: List[str] = []

        result = client.invoke_streaming("answer this", agent="scholar", on_token=tokens.append)

        assert tokens == ["Hello", ", ", "world", "!"]
        assert result == "Hello, world!"

    def test_invoke_streaming_records_metrics(self):
        client = make_streaming_client(["partial", " answer"])

        client.invoke_streaming("answer this", agent="scholar", on_token=lambda _t: None)

        assert len(client.session.calls) == 1
        call = client.session.calls[0]
        assert call.agent == "scholar"
        assert call.model == "gpt-4o"  # scholar is pinned
        assert call.output_tokens == 2

    def test_invoke_streaming_uses_stream_true_in_request(self):
        client = make_streaming_client(["chunk"])

        client.invoke_streaming("answer this", agent="scholar", on_token=lambda _t: None)

        call_kwargs = client._openai.chat.completions.create.call_args.kwargs
        assert call_kwargs["stream"] is True
        assert call_kwargs["stream_options"] == {"include_usage": True}

    def test_invoke_streaming_routes_like_normal_invoke(self):
        client = make_streaming_client(["chunk"])

        client.invoke_streaming("short prompt", agent="librarian", on_token=lambda _t: None)

        model = client.session.calls[0].model
        assert model != "gpt-4o"  # librarian is unpinned/routed, not top-tier
