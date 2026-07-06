"""
Regression tests for OptimizedLLMClient.invoke() against the real call shape
agents use: with_retry(llm_client.invoke, prompt_string, agent="critic").

Before this test existed, nothing exercised OptimizedLLMClient against an
actual agent-style call — the previous invoke(agent, payload, ...) signature
did not match what backend/agents/*.py calls, and would raise TypeError the
moment ENABLE_TOKEN_OPTIMIZER=true was set. These tests pin the fixed
contract in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from backend.optimization.configs.echograph import EchoGraphConfig
from backend.optimization.engine import OptimizedLLMClient


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Message:
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class _FakeResponse:
    choices: List[_Choice]
    usage: _Usage


def _fake_openai_client(content: str = "CONTRADICTION: no\nREASON: N/A\nCREDIBILITY: N/A"):
    """A stand-in for `openai.OpenAI()` — only chat.completions.create is used."""
    client = MagicMock()
    client.chat.completions.create.return_value = _FakeResponse(
        choices=[_Choice(message=_Message(content=content))],
        usage=_Usage(prompt_tokens=120, completion_tokens=40),
    )
    return client


def make_client(content: str = "ok") -> OptimizedLLMClient:
    client = OptimizedLLMClient(config=EchoGraphConfig, openai_api_key="sk-test")
    client._openai = _fake_openai_client(content)
    return client


class TestInvokeMatchesAgentCallShape:
    """Mirrors exactly how backend/agents/*.py calls the client."""

    def test_invoke_accepts_prompt_positional_and_agent_kwarg(self):
        client = make_client()
        result = client.invoke("Some fully-built prompt string", agent="critic")
        assert result == "ok"

    def test_invoke_works_for_every_real_agent_name(self):
        client = make_client()
        for agent in ("librarian", "philosopher", "critic", "synthesizer", "scholar"):
            result = client.invoke(f"prompt for {agent}", agent=agent)
            assert isinstance(result, str)

    def test_invoke_defaults_agent_when_omitted(self):
        client = make_client()
        # with_retry always passes agent=..., but invoke() should not require it.
        result = client.invoke("some prompt")
        assert result == "ok"

    def test_with_retry_style_call_does_not_raise(self):
        """Reproduces backend.retry.with_retry(llm_client.invoke, prompt, agent="critic")."""
        from backend.retry import with_retry

        client = make_client("CONTRADICTION: no\nREASON: N/A\nCREDIBILITY: N/A")
        result = with_retry(client.invoke, "prompt text", agent="critic")
        assert "CONTRADICTION" in result


class TestInvokeRoutingAndMetrics:
    def test_routed_agent_records_metrics_under_real_agent_name(self):
        client = make_client()
        client.invoke("short prompt", agent="librarian")
        assert len(client.session.calls) == 1
        assert client.session.calls[0].agent == "librarian"

    def test_pinned_agent_uses_configured_model(self):
        client = make_client()
        client.invoke("resolve this contradiction", agent="synthesizer")
        assert client.session.calls[0].model == "gpt-4o"

    def test_unpinned_agent_is_routed_by_complexity_not_hardcoded(self):
        client = make_client()
        client.invoke("extract concepts from this short document", agent="librarian")
        model = client.session.calls[0].model
        # librarian base_complexity=0.35 → nano or mini, never the top-tier model
        assert model != "gpt-4o"

    def test_multiple_calls_accumulate_session_cost(self):
        client = make_client()
        client.invoke("p1", agent="librarian")
        client.invoke("p2", agent="critic")
        client.invoke("p3", agent="scholar")
        assert len(client.session.calls) == 3
        assert client.session.total_cost_usd > 0

    def test_invoke_structured_still_applies_compression(self):
        """The structured entrypoint (payload-based) remains available for
        callers other than the raw-prompt agent path, e.g. eval tooling."""
        client = make_client()
        payload = {
            "id": "n1",
            "concept": "Test",
            "summary": "Summary text",
            "source": "doc.pdf",
            "confidence": 0.9,
            "embedding": [0.1] * 1536,
            "created_at": "2026-01-01T00:00:00Z",
            "times_retrieved": 3,
        }
        result = client.invoke_structured("philosopher", payload=payload)
        assert isinstance(result, str)
        assert client.session.calls[-1].compression_ratio > 0
