"""LLM client module for OpenAI integration with demo-mode fallback."""

from __future__ import annotations

import hashlib
import random
import time
from typing import Callable, List, Optional

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from backend.config import get_settings


class DemoLLMClient:
    """Deterministic fallback client when OpenAI is not configured."""

    def __init__(self):
        self.is_demo = True
        self._embedding_cache: dict[str, List[float]] = {}
        self._cache_limit = 512

    @staticmethod
    def _seeded_rng(text: str) -> random.Random:
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        return random.Random(int(digest[:16], 16))

    def embed_text(self, text: str) -> List[float]:
        cached = self._embedding_cache.get(text)
        if cached is not None:
            return list(cached)

        rng = self._seeded_rng(text)
        embedding = [rng.uniform(-1.0, 1.0) for _ in range(1536)]
        if len(self._embedding_cache) >= self._cache_limit:
            self._embedding_cache.pop(next(iter(self._embedding_cache)))
        self._embedding_cache[text] = embedding
        return list(embedding)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_text(text) for text in texts]

    def invoke(self, prompt: str) -> str:
        prompt_lower = prompt.lower()

        if "extract 5-10 key concepts" in prompt_lower:
            lines = [line.strip() for line in prompt.splitlines() if line.strip()]
            content_lines = [line for line in lines if not line.lower().startswith("document:")]
            concepts = []
            for idx, line in enumerate(content_lines[:5], start=1):
                title = line[:80] if len(line) > 12 else f"Concept {idx}"
                concepts.append(
                    f"CONCEPT: {title}\nSUMMARY: {line[:240]}"
                )
            if not concepts:
                concepts = [
                    "CONCEPT: General Insight\nSUMMARY: No detailed content provided; ingestion captured a generic concept.",
                ]
            return "\n\n".join(concepts)

        if "relationship:" in prompt_lower and "determine if there is a meaningful semantic relationship" in prompt_lower:
            return "RELATIONSHIP: extends\nSTRENGTH: 0.62\nEXPLANATION: The concepts discuss related themes and one expands the other."

        if "contradiction:" in prompt_lower and "do they contradict" in prompt_lower:
            # Conservative default to avoid excessive false positives in demo mode.
            return "CONTRADICTION: no\nREASON: N/A\nCREDIBILITY: N/A"

        if "synthesis:" in prompt_lower and "resolve this contradiction" in prompt_lower:
            return (
                "SYNTHESIS: The strongest interpretation keeps both claims in scope by applying each to different contexts and evidence quality.\n"
                "CONFIDENCE: 0.72\n"
                "REASONING: The synthesis balances source credibility and scope assumptions."
            )

        if "knowledge base:" in prompt_lower and "important: cite specific node ids" in prompt_lower:
            ids: List[str] = []
            for line in prompt.splitlines():
                if line.startswith("[Node #"):
                    token = line.split("[Node #", 1)[1].split("]", 1)[0]
                    ids.append(token)
            if not ids:
                return "I do not have enough knowledge in the current graph to answer confidently."
            references = ", ".join(f"node #{node_id}" for node_id in ids[:3])
            return f"According to {references}, the available evidence suggests a consistent answer based on the retrieved knowledge nodes."

        return "Processed successfully in demo mode."

    def invoke_streaming(self, prompt: str, agent: str, on_token: Callable[[str], None]) -> str:
        """
        No real model to stream from in demo mode — chunk the scripted
        response word-by-word so callers see the same on_token/return-value
        contract as the real streaming clients. `agent` is accepted for a
        uniform call shape across clients but unused (no routing in demo mode).
        """
        full_text = self.invoke(prompt)
        words = full_text.split(" ")
        for idx, word in enumerate(words):
            chunk = word if idx == 0 else " " + word
            on_token(chunk)
        return full_text


class LLMClient:
    """Client for OpenAI LLM and embeddings with lightweight retry logic."""

    def __init__(self):
        settings = get_settings()

        if not settings.is_openai_configured:
            raise ValueError("OpenAI API key not configured")

        self.is_demo = False
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=settings.openai_api_key,
        )
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.4,
            openai_api_key=settings.openai_api_key,
        )
        self._embedding_cache: dict[str, List[float]] = {}
        self._cache_limit = 512

    @staticmethod
    def _retry(fn, *args, **kwargs):
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - depends on provider failures
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.35 * (2**attempt))
        raise last_error if last_error else RuntimeError("Unknown LLM client error")

    def embed_text(self, text: str) -> List[float]:
        cached = self._embedding_cache.get(text)
        if cached is not None:
            return list(cached)

        embedding = self._retry(self.embeddings.embed_query, text)
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
            generated = self._retry(self.embeddings.embed_documents, missing_texts)
            for idx, text, embedding in zip(missing_indices, missing_texts, generated):
                if len(self._embedding_cache) >= self._cache_limit:
                    self._embedding_cache.pop(next(iter(self._embedding_cache)))
                self._embedding_cache[text] = embedding
                results[idx] = list(embedding)

        return [embedding if embedding is not None else [0.0] * 1536 for embedding in results]

    def invoke(self, prompt: str) -> str:
        response = self._retry(self.llm.invoke, prompt)
        return response.content

    def invoke_streaming(self, prompt: str, agent: str, on_token: Callable[[str], None]) -> str:
        """
        Stream the response token-by-token, calling on_token(chunk) as each
        arrives, and return the full accumulated text once the stream ends.
        Retries the whole call on transient failure (partial output from a
        failed attempt is discarded, not partially delivered twice). `agent`
        is accepted for a uniform call shape but unused here (no per-agent
        routing in the non-optimized client).
        """
        def _run_stream() -> str:
            chunks: List[str] = []
            for chunk in self.llm.stream(prompt):
                text = chunk.content or ""
                if not text:
                    continue
                chunks.append(text)
                on_token(text)
            return "".join(chunks)

        return self._retry(_run_stream)


_llm_client: Optional[object] = None


def get_llm_client() -> object:
    """
    Get a singleton LLM client.

    Resolution order:
      1. Demo mode / no API key  → DemoLLMClient (no network calls)
      2. ENABLE_TOKEN_OPTIMIZER=true → OptimizedLLMClient (full optimization stack)
      3. Default → LLMClient (original, unchanged behaviour)
    """
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    settings = get_settings()

    if settings.demo_mode or not settings.is_openai_configured:
        _llm_client = DemoLLMClient()
        return _llm_client

    if settings.enable_token_optimizer:
        from backend.optimization.engine import OptimizedLLMClient
        from backend.optimization.configs.echograph import EchoGraphConfig
        _llm_client = OptimizedLLMClient(
            config=EchoGraphConfig,
            openai_api_key=settings.openai_api_key,
        )
        return _llm_client

    _llm_client = LLMClient()
    return _llm_client
