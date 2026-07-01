"""
Base config types — the contract every top-layer config must satisfy.

A config is just a dataclass.  No inheritance required — duck typing
means any object with these attributes works as a config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AgentConfig:
    """
    Per-agent optimization settings.

    fields:           Which payload keys to keep after compression.
                      Empty list = no compression (pass through everything).
    model:            Pin to a specific model, bypassing routing.
                      None = let ModelRouter decide based on complexity.
    base_complexity:  0.0–1.0 score used by ModelRouter.
                      0.0 = trivial extraction → nano
                      0.5 = balanced reasoning → mini
                      1.0 = deep reasoning / user-facing → 4o
    temperature:      LLM temperature for this agent.
    system_prompt:    Static system prompt cached across calls.
                      None = use config.default_system_prompt.
    """
    fields:           List[str]         = field(default_factory=list)
    model:            Optional[str]     = None
    base_complexity:  float             = 0.5
    temperature:      float             = 0.3
    system_prompt:    Optional[str]     = None


@dataclass
class OptimizationConfig:
    """
    Top-level config passed to OptimizedLLMClient.

    agents:                 Map of agent_name → AgentConfig.
    default_model:          Fallback model when routing produces no result.
    default_system_prompt:  Fallback system prompt for agents with no override.
    max_tokens_per_call:    Hard budget used for budget_used_pct metric and
                            context-length complexity penalty.
    cache_prefix_tokens:    Expected size of the static system prompt prefix.
                            Used only for cost-savings estimation in metrics.
    """
    agents:                 Dict[str, AgentConfig]
    default_model:          str   = "gpt-4o-mini"
    default_system_prompt:  str   = "You are a helpful AI assistant. Respond concisely and accurately."
    max_tokens_per_call:    int   = 2_000
    cache_prefix_tokens:    int   = 400
