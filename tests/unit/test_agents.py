"""Unit tests for agent nodes with deterministic fake collaborators."""

from __future__ import annotations

from typing import Dict, List

from backend.agents.critic import critic_node
from backend.agents.librarian import librarian_node
from backend.agents.philosopher import philosopher_node
from backend.agents.scholar import scholar_node
from backend.agents.synthesizer import synthesizer_node


class FakeLLM:
    def embed_text(self, _text: str) -> List[float]:
        return [0.01] * 1536

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [[0.01] * 1536 for _ in texts]

    def invoke(self, prompt: str) -> str:
        if "Extract 5-10 key concepts" in prompt:
            return (
                "CONCEPT: Knowledge Graphs\n"
                "SUMMARY: Knowledge graphs connect entities and relationships.\n\n"
                "CONCEPT: Contradiction Resolution\n"
                "SUMMARY: Contradictions can be synthesized into nuanced conclusions."
            )
        if "RELATIONSHIP:" in prompt and "knowledge graph expert" in prompt.lower():
            return "RELATIONSHIP: supports\nSTRENGTH: 0.8\nEXPLANATION: Concepts are semantically aligned."
        if "CONTRADICTION:" in prompt and "critical thinking expert" in prompt.lower():
            return "CONTRADICTION: yes\nREASON: Claims conflict on outcomes.\nCREDIBILITY: Peer reviewed source is stronger."
        if "SYNTHESIS:" in prompt and "knowledge synthesis expert" in prompt.lower():
            return (
                "SYNTHESIS: Both claims can hold under different assumptions.\n"
                "CONFIDENCE: 0.74\n"
                "REASONING: Context-specific evidence reconciles conflict."
            )
        if "You answer questions" in prompt:
            return "According to node #[node-a], the answer is grounded in retrieved evidence."
        return "Processed"

    def invoke_streaming(self, prompt: str, agent: str, on_token) -> str:
        full_text = self.invoke(prompt)
        on_token(full_text)
        return full_text


class FakeStore:
    def __init__(self):
        self.updated_ids: List[str] = []

    def search_similar(self, _embedding: List[float], top_k: int = 5, threshold: float = 0.0):
        return [
            {
                "id": "node-existing",
                "concept": "Existing Concept",
                "summary": "Existing summary",
                "source": "existing.txt",
                "node_type": "raw",
                "confidence": 1.0,
                "contradiction_resolved": False,
                "connected_to": [],
                "relationship_types": [],
                "times_retrieved": 0,
                "created_at": "",
                "similarity": 0.95,
            }
        ]

    def get_node(self, node_id: str):
        if node_id == "node-existing":
            return {
                "id": node_id,
                "concept": "Existing Concept",
                "summary": "Existing summary",
                "source": "existing.txt",
                "node_type": "raw",
                "confidence": 1.0,
                "contradiction_resolved": False,
                "connected_to": [],
                "relationship_types": [],
                "times_retrieved": 0,
                "created_at": "",
            }
        return None

    def update_retrieval_count(self, node_id: str):
        self.updated_ids.append(node_id)


def _base_state() -> Dict:
    return {
        "input_type": "document",
        "raw_content": "Sample content",
        "source_label": "sample.txt",
        "existing_nodes": [],
        "new_concepts": [],
        "contradictions": [],
        "connections": [],
        "resolutions": [],
        "query_text": "",
        "retrieved_nodes": [],
        "final_answer": "",
        "current_agent": "",
        "processing_complete": False,
        "contradiction_found": False,
        "resolution_confidence": 1.0,
        "loop_count": 0,
        "session_id": "test-session",
        "agent_events": [],
        "event_callback": lambda _event: None,
    }


def test_librarian_node_extracts_concepts(monkeypatch):
    monkeypatch.setattr("backend.agents.librarian.get_llm_client", lambda: FakeLLM())
    store = FakeStore()
    state = _base_state()

    result = librarian_node(state, store)

    assert len(result["new_concepts"]) >= 1
    assert result["current_agent"] == "librarian"
    assert any(event["event"] == "concept_extracted" for event in result["agent_events"])


def test_philosopher_node_discovers_connections(monkeypatch):
    monkeypatch.setattr("backend.agents.philosopher.get_llm_client", lambda: FakeLLM())
    state = _base_state()
    state["new_concepts"] = [
        {
            "id": "temp-a",
            "concept": "Concept A",
            "summary": "Summary A",
            "source": "a.txt",
            "embedding": [0.01] * 1536,
        }
    ]
    state["existing_nodes"] = [
        {
            "id": "node-existing",
            "concept": "Concept B",
            "summary": "Summary B",
            "source": "b.txt",
        }
    ]

    result = philosopher_node(state)

    assert len(result["connections"]) == 1
    assert result["connections"][0]["relationship_type"] == "supports"


def test_critic_node_flags_contradictions(monkeypatch):
    monkeypatch.setattr("backend.agents.critic.get_llm_client", lambda: FakeLLM())
    store = FakeStore()
    state = _base_state()
    state["new_concepts"] = [
        {
            "id": "temp-a",
            "concept": "Concept A",
            "summary": "Summary A",
            "source": "a.txt",
            "embedding": [0.01] * 1536,
        }
    ]

    result = critic_node(state, store)

    assert result["contradiction_found"] is True
    assert len(result["contradictions"]) == 1


def test_synthesizer_node_resolves_contradictions(monkeypatch):
    monkeypatch.setattr("backend.agents.synthesizer.get_llm_client", lambda: FakeLLM())
    store = FakeStore()
    state = _base_state()
    state["loop_count"] = 1
    state["contradictions"] = [
        {
            "old_node_id": "node-existing",
            "new_concept_id": "temp-a",
            "new_concept": "Concept A",
            "new_concept_summary": "Summary A",
            "reason": "Conflict",
            "old_source": "existing.txt",
            "new_source": "a.txt",
            "credibility_assessment": "existing stronger",
        }
    ]

    result = synthesizer_node(state, store)

    assert result["resolution_confidence"] > 0
    assert len(result["resolutions"]) == 1


def test_scholar_node_generates_answer(monkeypatch):
    monkeypatch.setattr("backend.agents.scholar.get_llm_client", lambda: FakeLLM())
    store = FakeStore()
    state = _base_state()
    state["input_type"] = "query"
    state["query_text"] = "What is this?"

    result = scholar_node(state, store)

    assert "According to node" in result["final_answer"]
    assert len(result["retrieved_nodes"]) >= 1
    assert store.updated_ids


def test_scholar_node_emits_agent_token_events(monkeypatch):
    monkeypatch.setattr("backend.agents.scholar.get_llm_client", lambda: FakeLLM())
    store = FakeStore()
    state = _base_state()
    state["input_type"] = "query"
    state["query_text"] = "What is this?"

    result = scholar_node(state, store)

    token_events = [e for e in result["agent_events"] if e["event"] == "agent_token"]
    assert token_events, "expected at least one agent_token event during streaming"
    assert all(e["agent"] == "scholar" for e in token_events)
    reconstructed = "".join(e["data"]["token"] for e in token_events)
    assert reconstructed.strip() == result["final_answer"]


def test_scholar_node_calls_streaming_not_plain_invoke(monkeypatch):
    class StrictStreamingOnlyLLM(FakeLLM):
        """invoke_streaming here does NOT delegate to invoke — if scholar_node
        called plain invoke() instead of invoke_streaming(), this test would
        fail with the AssertionError below rather than silently passing."""

        def invoke(self, prompt: str) -> str:
            raise AssertionError("scholar should call invoke_streaming, not invoke")

        def invoke_streaming(self, prompt: str, agent: str, on_token) -> str:
            answer = "According to node #[node-a], streamed answer."
            on_token(answer)
            return answer

    monkeypatch.setattr("backend.agents.scholar.get_llm_client", lambda: StrictStreamingOnlyLLM())
    store = FakeStore()
    state = _base_state()
    state["input_type"] = "query"
    state["query_text"] = "What is this?"

    result = scholar_node(state, store)

    assert "According to node" in result["final_answer"]
