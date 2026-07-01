"""
OptimizedLLMClient — Layer 1 base engine.

Wraps the OpenAI API with prompt caching, payload compression,
model routing, schema enforcement, and per-call cost/latency tracking.
All behaviour is driven by an OptimizationConfig injected at construction
time so the engine itself never changes between use cases.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Type

from backend.optimization.metrics import CallMetrics, SessionMetrics
from backend.optimization.middleware import (
    ModelRouter,
    PayloadCompressor,
    PromptCacheManager,
    SchemaEnforcer,
    TokenCounter,
)

logger = logging.getLogger(__name__)


class OptimizedLLMClient:
    """
    Drop-in replacement for LLMClient that applies the full optimization
    stack before every LLM call.

    Usage
    -----
    client = OptimizedLLMClient(config=EchoGraphConfig, openai_api_key="sk-...")
    result = client.invoke(agent="librarian", payload={"concept": "...", "summary": "..."})
    print(client.session.summary())
    """

    def __init__(self, config, openai_api_key: str):
        from openai import OpenAI

        self.config = config
        self._openai = OpenAI(api_key=openai_api_key)

        self.cache_manager = PromptCacheManager(config)
        self.compressor = PayloadCompressor(config)
        self.router = ModelRouter(config)
        self.enforcer = SchemaEnforcer(config)
        self.counter = TokenCounter()
        self.session = SessionMetrics()

        self._embedding_cache: Dict[str, List[float]] = {}
        self._cache_limit = 512

    def invoke(
        self,
        agent: str,
        payload: Dict[str, Any],
        response_schema: Optional[Dict] = None,
        raw_prompt: Optional[str] = None,
    ) -> str:
        """
        Run one optimized LLM call for the given agent.

        Parameters
        ----------
        agent:           Agent name key — must match a key in config.agents.
        payload:         Raw node/context dict — will be compressed before sending.
        response_schema: Optional JSON Schema dict for structured output enforcement.
        raw_prompt:      If provided, skip compression and use this as the user message.
        """
        t_start = time.perf_counter()

        # 1. Compress payload to only the fields this agent needs.
        if raw_prompt is None:
            compressed = self.compressor.compress(agent, payload)
            user_content = json.dumps(compressed, separators=(",", ":"))
        else:
            user_content = raw_prompt
            compressed = payload

        # 2. Get cached system prompt prefix for this agent.
        system_prompt = self.cache_manager.get_system_prompt(agent)
        cache_hit = self.cache_manager.is_cached(agent)

        # 3. Route to the cheapest model that meets quality requirements.
        model = self.router.select_model(agent, user_content)

        # 4. Build messages array.
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ]

        # 5. Build call kwargs — inject schema if provided.
        call_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.config.agents[agent].temperature
                if hasattr(self.config.agents.get(agent, object()), "temperature")
                else 0.3,
        }
        if response_schema:
            call_kwargs = self.enforcer.apply(call_kwargs, response_schema)

        # 6. Make the API call.
        try:
            response = self._openai.chat.completions.create(**call_kwargs)
        except Exception:
            logger.exception("OptimizedLLMClient: API call failed for agent=%s model=%s", agent, model)
            raise

        content = response.choices[0].message.content or ""

        # 7. Count tokens and cost.
        input_tokens  = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost_usd      = self.router.estimate_cost(model, input_tokens, output_tokens)
        latency_ms    = (time.perf_counter() - t_start) * 1000

        original_tokens = self.counter.estimate(json.dumps(payload, separators=(",", ":")))
        compressed_tokens = self.counter.estimate(user_content)
        compression_ratio = 1.0 - (compressed_tokens / max(original_tokens, 1))

        metrics = CallMetrics(
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=cache_hit,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            compression_ratio=max(0.0, compression_ratio),
            budget_used_pct=(input_tokens / self.config.max_tokens_per_call) * 100,
        )
        self.session.record(metrics)

        logger.info(
            "agent=%s model=%s tokens=%d/%d cost=$%.5f latency=%.0fms cache=%s compression=%.0f%%",
            agent, model, input_tokens, output_tokens,
            cost_usd, latency_ms, cache_hit,
            compression_ratio * 100,
        )

        return content

    def invoke_raw(self, prompt: str, model: Optional[str] = None) -> str:
        """
        Bypass compression/routing — send a raw prompt with no config lookup.
        Used by agents that build their own prompts (e.g. librarian, scholar).
        Still records metrics under agent='_raw'.
        """
        t_start = time.perf_counter()
        chosen_model = model or self.config.default_model

        response = self._openai.chat.completions.create(
            model=chosen_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        content = response.choices[0].message.content or ""

        input_tokens  = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost_usd      = self.router.estimate_cost(chosen_model, input_tokens, output_tokens)
        latency_ms    = (time.perf_counter() - t_start) * 1000

        metrics = CallMetrics(
            agent="_raw",
            model=chosen_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=False,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            compression_ratio=0.0,
            budget_used_pct=(input_tokens / self.config.max_tokens_per_call) * 100,
        )
        self.session.record(metrics)
        return content

    def embed_text(self, text: str) -> List[float]:
        cached = self._embedding_cache.get(text)
        if cached is not None:
            return list(cached)

        response = self._openai.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        embedding = response.data[0].embedding
        if len(self._embedding_cache) >= self._cache_limit:
            self._embedding_cache.pop(next(iter(self._embedding_cache)))
        self._embedding_cache[text] = embedding
        return list(embedding)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        results: List[Optional[List[float]]] = [None] * len(texts)
        missing_indices: List[int] = []
        missing_texts: List[str] = []

        for idx, text in enumerate(texts):
            cached = self._embedding_cache.get(text)
            if cached is not None:
                results[idx] = list(cached)
            else:
                missing_indices.append(idx)
                missing_texts.append(text)

        if missing_texts:
            response = self._openai.embeddings.create(
                model="text-embedding-3-small",
                input=missing_texts,
            )
            for list_idx, (orig_idx, text) in enumerate(zip(missing_indices, missing_texts)):
                embedding = response.data[list_idx].embedding
                if len(self._embedding_cache) >= self._cache_limit:
                    self._embedding_cache.pop(next(iter(self._embedding_cache)))
                self._embedding_cache[text] = embedding
                results[orig_idx] = embedding

        return [r if r is not None else [0.0] * 1536 for r in results]

    @property
    def is_demo(self) -> bool:
        return False
