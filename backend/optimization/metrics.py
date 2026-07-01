"""
Metrics — token/cost/latency tracking per call and per session.

Every OptimizedLLMClient call produces a CallMetrics record.
SessionMetrics accumulates them and can emit a summary log or
return a dict for the /graph/stats or a future /metrics endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class CallMetrics:
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    cost_usd: float
    latency_ms: float
    compression_ratio: float  # 0.0 = no compression, 1.0 = 100% smaller
    budget_used_pct: float    # input_tokens / max_tokens_per_call * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent":             self.agent,
            "model":             self.model,
            "input_tokens":      self.input_tokens,
            "output_tokens":     self.output_tokens,
            "cache_hit":         self.cache_hit,
            "cost_usd":          round(self.cost_usd, 6),
            "latency_ms":        round(self.latency_ms, 1),
            "compression_ratio": round(self.compression_ratio, 3),
            "budget_used_pct":   round(self.budget_used_pct, 1),
        }


@dataclass
class SessionMetrics:
    """
    Accumulates CallMetrics for a single ingestion/query session.

    Tracks actual cost vs. what the same calls would have cost with:
      - No compression  (full node payloads)
      - No model routing (all gpt-4o)
      - No caching      (full system prompt every time)

    This gives the real savings figure, not a theoretical one.
    """

    calls: List[CallMetrics] = field(default_factory=list)

    # Baseline assumptions for savings calculation
    UNOPTIMIZED_TOKENS_PER_CALL = 4_200
    UNOPTIMIZED_MODEL_COST_PER_1M = 2.50  # gpt-4o input price

    def record(self, metrics: CallMetrics) -> None:
        self.calls.append(metrics)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def cache_hits(self) -> int:
        return sum(1 for c in self.calls if c.cache_hit)

    @property
    def avg_latency_ms(self) -> float:
        if not self.calls:
            return 0.0
        return sum(c.latency_ms for c in self.calls) / len(self.calls)

    @property
    def avg_compression_ratio(self) -> float:
        if not self.calls:
            return 0.0
        return sum(c.compression_ratio for c in self.calls) / len(self.calls)

    @property
    def estimated_unoptimized_cost(self) -> float:
        n = len(self.calls)
        tokens = n * self.UNOPTIMIZED_TOKENS_PER_CALL
        return (tokens * self.UNOPTIMIZED_MODEL_COST_PER_1M) / 1_000_000

    @property
    def savings_pct(self) -> float:
        baseline = self.estimated_unoptimized_cost
        if baseline == 0:
            return 0.0
        saved = baseline - self.total_cost_usd
        return round((saved / baseline) * 100, 1)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_calls":                len(self.calls),
            "total_input_tokens":         self.total_input_tokens,
            "total_output_tokens":        self.total_output_tokens,
            "total_tokens":               self.total_tokens,
            "total_cost_usd":             round(self.total_cost_usd, 6),
            "estimated_unoptimized_cost": round(self.estimated_unoptimized_cost, 6),
            "savings_pct":                self.savings_pct,
            "cache_hits":                 self.cache_hits,
            "avg_latency_ms":             round(self.avg_latency_ms, 1),
            "avg_compression_ratio":      round(self.avg_compression_ratio, 3),
            "calls":                      [c.to_dict() for c in self.calls],
        }

    def log_summary(self) -> None:
        s = self.summary()
        logger.info(
            "Session complete | calls=%d tokens=%d cost=$%.5f savings=%.1f%% "
            "cache_hits=%d avg_latency=%.0fms compression=%.0f%%",
            s["total_calls"],
            s["total_tokens"],
            s["total_cost_usd"],
            s["savings_pct"],
            s["cache_hits"],
            s["avg_latency_ms"],
            s["avg_compression_ratio"] * 100,
        )

    def reset(self) -> None:
        self.calls.clear()
