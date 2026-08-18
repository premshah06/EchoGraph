"""
Optimization Middleware — Layer 2.

Five stateless components that the engine composes per call:
  PromptCacheManager  — static system prompt prefix management
  PayloadCompressor   — strips fields agents don't need
  ModelRouter         — maps complexity → cheapest viable model
  SchemaEnforcer      — injects structured output format
  TokenCounter        — fast approximate token estimation
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table  (USD per 1M tokens, input / output)
# Update these when OpenAI changes prices.
# ---------------------------------------------------------------------------
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o":            {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":       {"input": 0.15,  "output": 0.60},
    "gpt-4.1-nano":      {"input": 0.10,  "output": 0.40},
    "gpt-4-turbo":       {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo":     {"input": 0.50,  "output": 1.50},
}

# Complexity thresholds for model selection
COMPLEXITY_MINI_MAX  = 0.45   # below → nano; below this and above nano → mini
COMPLEXITY_4O_MIN    = 0.70   # at or above → full 4o


class PromptCacheManager:
    """
    Manages static system prompt prefixes per agent.

    The system prompt is the largest fixed cost per call.  By keeping it
    identical across calls we benefit from OpenAI's prompt-caching (50%
    discount on cached prefix tokens ≥ 1024 tokens).

    Tracks which agents have been called at least once in this process
    lifetime so callers can log cache-hit savings.
    """

    def __init__(self, config):
        self.config = config
        self._called: Set[str] = set()

    def get_system_prompt(self, agent: str) -> str:
        agent_cfg = self.config.agents.get(agent)
        if agent_cfg and agent_cfg.system_prompt:
            return agent_cfg.system_prompt
        return self.config.default_system_prompt

    def is_cached(self, agent: str) -> bool:
        """Return True if this agent's prompt has been sent before (cache-warm)."""
        was_cached = agent in self._called
        self._called.add(agent)
        return was_cached

    def cache_stats(self) -> Dict[str, Any]:
        return {"warmed_agents": list(self._called), "count": len(self._called)}


class PayloadCompressor:
    """
    Strips node/context payloads to only the fields each agent actually reads.

    Compression ratio example for a full EchoGraph node:
      Full node  ≈ 380 tokens  (includes embedding, created_at, times_retrieved, …)
      Compressed ≈  90 tokens  (concept + summary only for librarian)
      Saving     ≈  76%
    """

    def __init__(self, config):
        self.config = config

    def compress(self, agent: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        agent_cfg = self.config.agents.get(agent)
        if not agent_cfg or not agent_cfg.fields:
            # No compression map defined → pass through as-is.
            return payload

        allowed: Set[str] = set(agent_cfg.fields)

        if isinstance(payload, list):
            return [self._filter(item, allowed) for item in payload]
        return self._filter(payload, allowed)

    def compress_list(self, agent: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.compress(agent, item) for item in items]

    @staticmethod
    def _filter(obj: Dict[str, Any], allowed: Set[str]) -> Dict[str, Any]:
        return {k: v for k, v in obj.items() if k in allowed}

    def compression_ratio(self, agent: str, payload: Dict[str, Any]) -> float:
        original_len = len(json.dumps(payload, separators=(",", ":")))
        compressed_len = len(json.dumps(self.compress(agent, payload), separators=(",", ":")))
        if original_len == 0:
            return 0.0
        return 1.0 - (compressed_len / original_len)


class ModelRouter:
    """
    Routes each agent call to the cheapest model that meets quality requirements.

    Complexity score is computed from:
      - Agent's declared base complexity (from config)
      - Context size relative to the token budget
      - Whether the response requires deep reasoning

    Score → Model mapping:
      0.00 – 0.44  →  gpt-4.1-nano  (fast, very cheap, simple extraction)
      0.45 – 0.69  →  gpt-4o-mini   (balanced, most tasks)
      0.70 – 1.00  →  gpt-4o        (full reasoning, user-facing answers)
    """

    def __init__(self, config):
        self.config = config

    def complexity_score(self, agent: str, context: str) -> float:
        agent_cfg = self.config.agents.get(agent)
        base = agent_cfg.base_complexity if agent_cfg else 0.5

        # Context length penalty — longer context needs smarter model.
        token_estimate = len(context) / 4
        budget = self.config.max_tokens_per_call
        length_factor = min(1.0, token_estimate / budget) * 0.25

        return min(1.0, base + length_factor)

    def select_model(self, agent: str, context: str) -> str:
        agent_cfg = self.config.agents.get(agent)

        # Config can pin a specific model; skip routing if so.
        if agent_cfg and agent_cfg.model:
            return agent_cfg.model

        score = self.complexity_score(agent, context)

        if score < COMPLEXITY_MINI_MAX:
            return "gpt-4.1-nano" if "gpt-4.1-nano" in MODEL_PRICING else "gpt-4o-mini"
        if score < COMPLEXITY_4O_MIN:
            return "gpt-4o-mini"
        return "gpt-4o"

    @staticmethod
    def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["gpt-4o"])
        return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

    def blended_cost_per_session(self, calls: List[Dict]) -> float:
        return sum(
            self.estimate_cost(c["model"], c["input_tokens"], c["output_tokens"])
            for c in calls
        )


class SchemaEnforcer:
    """
    Injects OpenAI structured output parameters into API call kwargs.

    When a response_schema is provided, the model is guaranteed to return
    valid JSON matching the schema — eliminating the ~12% retry rate from
    malformed free-text JSON responses.
    """

    def __init__(self, config):
        self.config = config

    def apply(self, call_kwargs: Dict[str, Any], schema: Dict) -> Dict[str, Any]:
        """
        Mutate call_kwargs in-place to add structured output enforcement.
        Returns the same dict for chaining.
        """
        call_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.get("title", "response"),
                "strict": True,
                "schema": schema,
            },
        }
        return call_kwargs

    @staticmethod
    def parse(content: str) -> Any:
        """Safe JSON parse — returns raw string if parse fails."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("SchemaEnforcer: failed to parse JSON response")
            return content


class TokenCounter:
    """
    Fast approximate token estimator — no tokenizer dependency.

    Uses the well-established heuristic: 1 token ≈ 4 characters for
    English prose.  Accurate to within ~10% for typical LLM payloads.

    For production-grade accuracy, swap with tiktoken:
      import tiktoken
      enc = tiktoken.encoding_for_model("gpt-4o")
      return len(enc.encode(text))
    """

    CHARS_PER_TOKEN = 4.0

    def estimate(self, text: str) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))

    def estimate_messages(self, messages: List[Dict[str, str]]) -> int:
        total = 0
        for msg in messages:
            total += self.estimate(msg.get("content", ""))
            total += 4  # role + formatting overhead per message
        return total + 3  # reply primer

    def budget_remaining(self, messages: List[Dict[str, str]], max_tokens: int) -> int:
        used = self.estimate_messages(messages)
        return max(0, max_tokens - used)

    def will_exceed_budget(self, messages: List[Dict[str, str]], max_tokens: int) -> bool:
        return self.estimate_messages(messages) > max_tokens
