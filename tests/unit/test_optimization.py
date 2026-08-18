"""Unit tests for the token optimization engine."""

from __future__ import annotations

import json

import pytest

from backend.optimization.configs.base import AgentConfig, OptimizationConfig
from backend.optimization.configs.graphmediator import GraphMediatorConfig
from backend.optimization.configs.project_builder import ProjectBuilderConfig
from backend.optimization.metrics import CallMetrics, SessionMetrics
from backend.optimization.middleware import (
    ModelRouter,
    PayloadCompressor,
    PromptCacheManager,
    SchemaEnforcer,
    TokenCounter,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def make_config(**agent_overrides) -> OptimizationConfig:
    agents = {
        "simple": AgentConfig(fields=["concept"], model="gpt-4o-mini", base_complexity=0.3),
        "complex": AgentConfig(fields=["concept", "summary", "source"], model="gpt-4o", base_complexity=0.8),
        "auto": AgentConfig(fields=["concept", "summary"], model=None, base_complexity=0.5),
    }
    agents.update(agent_overrides)
    return OptimizationConfig(agents=agents, max_tokens_per_call=1_600)


FULL_NODE = {
    "id": "node-1",
    "concept": "Transformer Architecture",
    "summary": "Self-attention mechanism for sequence modelling.",
    "source": "paper.pdf",
    "confidence": 0.95,
    "node_type": "raw",
    "embedding": [0.1] * 1536,
    "created_at": "2026-01-01T00:00:00Z",
    "times_retrieved": 42,
    "connected_to": ["node-2"],
    "relationship_types": ["supports"],
}


# ── PayloadCompressor ──────────────────────────────────────────────────────

class TestPayloadCompressor:
    def test_keeps_only_declared_fields(self):
        cfg = make_config()
        comp = PayloadCompressor(cfg)
        result = comp.compress("simple", FULL_NODE)
        assert set(result.keys()) == {"concept"}

    def test_passthrough_when_no_fields_declared(self):
        cfg = OptimizationConfig(
            agents={"any": AgentConfig(fields=[])},
            max_tokens_per_call=1_600,
        )
        comp = PayloadCompressor(cfg)
        result = comp.compress("any", FULL_NODE)
        assert result == FULL_NODE

    def test_passthrough_for_unknown_agent(self):
        cfg = make_config()
        comp = PayloadCompressor(cfg)
        result = comp.compress("nonexistent", FULL_NODE)
        assert result == FULL_NODE

    def test_compress_list(self):
        cfg = make_config()
        comp = PayloadCompressor(cfg)
        nodes = [FULL_NODE, {**FULL_NODE, "id": "node-2"}]
        result = comp.compress_list("simple", nodes)
        assert len(result) == 2
        assert all(set(r.keys()) == {"concept"} for r in result)

    def test_compression_ratio_is_positive(self):
        cfg = make_config()
        comp = PayloadCompressor(cfg)
        ratio = comp.compression_ratio("simple", FULL_NODE)
        assert 0.0 < ratio < 1.0

    def test_embedding_stripped_reduces_size_significantly(self):
        cfg = make_config()
        comp = PayloadCompressor(cfg)
        original = len(json.dumps(FULL_NODE))
        compressed = len(json.dumps(comp.compress("complex", FULL_NODE)))
        assert compressed < original * 0.5


# ── ModelRouter ────────────────────────────────────────────────────────────

class TestModelRouter:
    def test_pinned_model_is_returned_directly(self):
        cfg = make_config()
        router = ModelRouter(cfg)
        assert router.select_model("simple", "short context") == "gpt-4o-mini"
        assert router.select_model("complex", "short context") == "gpt-4o"

    def test_auto_routing_low_complexity_gives_cheap_model(self):
        cfg = OptimizationConfig(
            agents={"nano_task": AgentConfig(fields=[], model=None, base_complexity=0.1)},
            max_tokens_per_call=1_600,
        )
        router = ModelRouter(cfg)
        model = router.select_model("nano_task", "x" * 10)
        assert model in ("gpt-4.1-nano", "gpt-4o-mini")

    def test_auto_routing_high_complexity_gives_4o(self):
        cfg = OptimizationConfig(
            agents={"hard_task": AgentConfig(fields=[], model=None, base_complexity=0.85)},
            max_tokens_per_call=1_600,
        )
        router = ModelRouter(cfg)
        model = router.select_model("hard_task", "x" * 10)
        assert model == "gpt-4o"

    def test_long_context_pushes_complexity_up(self):
        cfg = OptimizationConfig(
            agents={"mid_task": AgentConfig(fields=[], model=None, base_complexity=0.45)},
            max_tokens_per_call=400,
        )
        router = ModelRouter(cfg)
        score_short = router.complexity_score("mid_task", "x" * 10)
        score_long  = router.complexity_score("mid_task", "x" * 2000)
        assert score_long > score_short

    def test_cost_estimate_mini_cheaper_than_4o(self):
        router = ModelRouter(make_config())
        cost_mini = router.estimate_cost("gpt-4o-mini", 1000, 500)
        cost_4o   = router.estimate_cost("gpt-4o",      1000, 500)
        assert cost_mini < cost_4o

    def test_cost_estimate_is_positive(self):
        router = ModelRouter(make_config())
        assert router.estimate_cost("gpt-4o-mini", 100, 50) > 0


# ── PromptCacheManager ─────────────────────────────────────────────────────

class TestPromptCacheManager:
    def test_first_call_not_cached(self):
        cfg = make_config()
        mgr = PromptCacheManager(cfg)
        assert mgr.is_cached("simple") is False

    def test_second_call_is_cached(self):
        cfg = make_config()
        mgr = PromptCacheManager(cfg)
        mgr.is_cached("simple")
        assert mgr.is_cached("simple") is True

    def test_agent_system_prompt_returned(self):
        cfg = OptimizationConfig(
            agents={"x": AgentConfig(fields=[], system_prompt="Custom prompt for X")},
            max_tokens_per_call=1_600,
        )
        mgr = PromptCacheManager(cfg)
        assert mgr.get_system_prompt("x") == "Custom prompt for X"

    def test_default_prompt_returned_for_unknown_agent(self):
        cfg = OptimizationConfig(
            agents={},
            max_tokens_per_call=1_600,
            default_system_prompt="Default system prompt.",
        )
        mgr = PromptCacheManager(cfg)
        assert mgr.get_system_prompt("unknown") == "Default system prompt."

    def test_cache_stats_tracks_warmed_agents(self):
        cfg = make_config()
        mgr = PromptCacheManager(cfg)
        mgr.is_cached("simple")
        mgr.is_cached("complex")
        stats = mgr.cache_stats()
        assert stats["count"] == 2
        assert "simple" in stats["warmed_agents"]


# ── SchemaEnforcer ─────────────────────────────────────────────────────────

class TestSchemaEnforcer:
    SCHEMA = {
        "title": "concept_response",
        "type": "object",
        "properties": {
            "concept":    {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["concept", "confidence"],
        "additionalProperties": False,
    }

    def test_injects_response_format(self):
        cfg = make_config()
        enforcer = SchemaEnforcer(cfg)
        kwargs = {"model": "gpt-4o-mini", "messages": []}
        result = enforcer.apply(kwargs, self.SCHEMA)
        assert "response_format" in result
        assert result["response_format"]["type"] == "json_schema"

    def test_schema_name_set_from_title(self):
        cfg = make_config()
        enforcer = SchemaEnforcer(cfg)
        kwargs = {}
        enforcer.apply(kwargs, self.SCHEMA)
        assert kwargs["response_format"]["json_schema"]["name"] == "concept_response"

    def test_parse_valid_json(self):
        parsed = SchemaEnforcer.parse('{"concept": "RAG", "confidence": 0.9}')
        assert parsed["concept"] == "RAG"

    def test_parse_invalid_json_returns_raw_string(self):
        raw = "not valid json {"
        result = SchemaEnforcer.parse(raw)
        assert result == raw


# ── TokenCounter ───────────────────────────────────────────────────────────

class TestTokenCounter:
    def test_estimate_nonempty(self):
        counter = TokenCounter()
        assert counter.estimate("Hello world") > 0

    def test_longer_text_more_tokens(self):
        counter = TokenCounter()
        assert counter.estimate("a" * 400) > counter.estimate("a" * 40)

    def test_estimate_messages(self):
        counter = TokenCounter()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user",   "content": "What is RAG?"},
        ]
        assert counter.estimate_messages(messages) > 0

    def test_budget_remaining_decreases_with_longer_messages(self):
        counter = TokenCounter()
        short_msgs = [{"role": "user", "content": "Hi"}]
        long_msgs  = [{"role": "user", "content": "x" * 4000}]
        assert counter.budget_remaining(short_msgs, 2000) > counter.budget_remaining(long_msgs, 2000)

    def test_will_exceed_budget(self):
        counter = TokenCounter()
        huge = [{"role": "user", "content": "x" * 40_000}]
        assert counter.will_exceed_budget(huge, 100) is True
        tiny = [{"role": "user", "content": "Hi"}]
        assert counter.will_exceed_budget(tiny, 2000) is False


# ── SessionMetrics ─────────────────────────────────────────────────────────

class TestSessionMetrics:
    def _make_call(self, agent="librarian", model="gpt-4o-mini",
                   input_tokens=300, output_tokens=150, cache_hit=False,
                   cost_usd=0.0001, latency_ms=320.0,
                   compression_ratio=0.72, budget_used_pct=18.75) -> CallMetrics:
        return CallMetrics(
            agent=agent, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_hit=cache_hit, cost_usd=cost_usd, latency_ms=latency_ms,
            compression_ratio=compression_ratio, budget_used_pct=budget_used_pct,
        )

    def test_empty_session_summary(self):
        session = SessionMetrics()
        s = session.summary()
        assert s["total_calls"] == 0
        assert s["total_cost_usd"] == 0.0

    def test_records_accumulate(self):
        session = SessionMetrics()
        session.record(self._make_call(input_tokens=300, output_tokens=150))
        session.record(self._make_call(input_tokens=200, output_tokens=100))
        assert session.total_input_tokens == 500
        assert session.total_output_tokens == 250
        assert len(session.calls) == 2

    def test_cache_hit_counting(self):
        session = SessionMetrics()
        session.record(self._make_call(cache_hit=True))
        session.record(self._make_call(cache_hit=False))
        session.record(self._make_call(cache_hit=True))
        assert session.cache_hits == 2

    def test_savings_pct_positive(self):
        session = SessionMetrics()
        for _ in range(5):
            session.record(self._make_call(cost_usd=0.0002))
        assert session.savings_pct > 0

    def test_reset_clears_calls(self):
        session = SessionMetrics()
        session.record(self._make_call())
        session.reset()
        assert session.calls == []

    def test_to_dict_has_required_keys(self):
        call = self._make_call()
        d = call.to_dict()
        for key in ("agent", "model", "input_tokens", "output_tokens",
                    "cache_hit", "cost_usd", "latency_ms",
                    "compression_ratio", "budget_used_pct"):
            assert key in d


# ── Config correctness ─────────────────────────────────────────────────────

class TestGraphMediatorConfig:
    def test_all_expected_agents_present(self):
        for agent in ("librarian", "philosopher", "critic", "synthesizer", "scholar"):
            assert agent in GraphMediatorConfig.agents

    def test_synthesizer_uses_4o(self):
        assert GraphMediatorConfig.agents["synthesizer"].model == "gpt-4o"

    def test_librarian_is_unpinned_for_routing(self):
        assert GraphMediatorConfig.agents["librarian"].model is None

    def test_philosopher_is_unpinned_for_routing(self):
        assert GraphMediatorConfig.agents["philosopher"].model is None

    def test_critic_is_unpinned_for_routing(self):
        assert GraphMediatorConfig.agents["critic"].model is None

    def test_synthesizer_and_scholar_remain_pinned(self):
        assert GraphMediatorConfig.agents["synthesizer"].model == "gpt-4o"
        assert GraphMediatorConfig.agents["scholar"].model == "gpt-4o"

    def test_critic_has_low_temperature(self):
        assert GraphMediatorConfig.agents["critic"].temperature <= 0.3

    def test_philosopher_strips_heavy_fields(self):
        fields = GraphMediatorConfig.agents["philosopher"].fields
        assert "embedding" not in fields
        assert "times_retrieved" not in fields

    def test_scholar_has_highest_complexity(self):
        complexities = {
            name: cfg.base_complexity
            for name, cfg in GraphMediatorConfig.agents.items()
        }
        assert complexities["scholar"] == max(complexities.values())


class TestProjectBuilderConfig:
    def test_all_expected_agents_present(self):
        for agent in ("planner", "coder", "reviewer", "documenter"):
            assert agent in ProjectBuilderConfig.agents

    def test_planner_uses_4o(self):
        assert ProjectBuilderConfig.agents["planner"].model == "gpt-4o"

    def test_documenter_uses_auto_routing(self):
        assert ProjectBuilderConfig.agents["documenter"].model is None

    def test_max_tokens_larger_than_graphmediator(self):
        assert ProjectBuilderConfig.max_tokens_per_call > GraphMediatorConfig.max_tokens_per_call

    def test_reviewer_lower_complexity_than_planner(self):
        assert (ProjectBuilderConfig.agents["reviewer"].base_complexity
                < ProjectBuilderConfig.agents["planner"].base_complexity)
