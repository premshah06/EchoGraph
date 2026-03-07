"""Integration tests for ingestion and query LangGraph workflows."""

from __future__ import annotations

from backend.agents import critic, librarian, philosopher, scholar, synthesizer
from backend.graphs.ingestion_graph import create_ingestion_graph
from backend.graphs.query_graph import create_query_graph
from backend.knowledge_store import KnowledgeStore


class GraphFakeLLM:
    def __init__(self, contradiction: bool = False, confidence: float = 0.8):
        self.contradiction = contradiction
        self.confidence = confidence

    def embed_text(self, _text: str):
        return [0.1] * 1536

    def embed_texts(self, texts):
        return [[0.1] * 1536 for _ in texts]

    def invoke(self, prompt: str) -> str:
        if "Extract 5-10 key concepts" in prompt:
            return "CONCEPT: Alpha\nSUMMARY: Alpha summary"
        if "knowledge graph expert" in prompt.lower():
            return "RELATIONSHIP: supports\nSTRENGTH: 0.7\nEXPLANATION: semantically aligned"
        if "critical thinking expert" in prompt.lower():
            if self.contradiction:
                return "CONTRADICTION: yes\nREASON: claims conflict\nCREDIBILITY: source A is stronger"
            return "CONTRADICTION: no\nREASON: N/A\nCREDIBILITY: N/A"
        if "knowledge synthesis expert" in prompt.lower():
            return (
                "SYNTHESIS: Reconciled statement.\n"
                f"CONFIDENCE: {self.confidence}\n"
                "REASONING: weighted evidence"
            )
        if "You answer questions" in prompt:
            return "According to node #[node-id], this is the answer."
        return "ok"


def patch_llm(monkeypatch, fake_llm: GraphFakeLLM):
    monkeypatch.setattr(librarian, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(philosopher, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(critic, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(synthesizer, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(scholar, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("backend.llm_client.get_llm_client", lambda: fake_llm)


def base_state():
    return {
        "input_type": "document",
        "raw_content": "Alpha content",
        "source_label": "doc.txt",
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
        "session_id": "integration-session",
        "agent_events": [],
        "event_callback": lambda _event: None,
    }


def test_ingestion_graph_without_contradiction(monkeypatch, tmp_path):
    fake_llm = GraphFakeLLM(contradiction=False)
    patch_llm(monkeypatch, fake_llm)

    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    graph = create_ingestion_graph(store)

    result = graph.invoke(base_state())

    assert result["processing_complete"] is True
    assert result["contradiction_found"] is False
    assert store.get_all_nodes()


def test_ingestion_graph_with_contradiction_resolution(monkeypatch, tmp_path):
    fake_llm = GraphFakeLLM(contradiction=True, confidence=0.82)
    patch_llm(monkeypatch, fake_llm)

    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    graph = create_ingestion_graph(store)

    result = graph.invoke(base_state())

    assert result["contradiction_found"] is True
    assert result["resolutions"]
    assert result["resolution_confidence"] >= 0.8


def test_ingestion_graph_enforces_max_loop_count(monkeypatch, tmp_path):
    fake_llm = GraphFakeLLM(contradiction=True, confidence=0.3)
    patch_llm(monkeypatch, fake_llm)

    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    graph = create_ingestion_graph(store)

    result = graph.invoke(base_state())

    assert result["loop_count"] == 3
    assert result["processing_complete"] is True


def test_query_graph_end_to_end(monkeypatch, tmp_path):
    fake_llm = GraphFakeLLM(contradiction=False)
    patch_llm(monkeypatch, fake_llm)

    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    store.add_node(
        {
            "concept": "Alpha",
            "summary": "Alpha summary",
            "source": "doc.txt",
            "node_type": "raw",
            "confidence": 1.0,
            "contradiction_resolved": False,
            "connected_to": [],
            "relationship_types": [],
            "embedding": [0.1] * 1536,
            "times_retrieved": 0,
        }
    )

    graph = create_query_graph(store)
    state = base_state()
    state["input_type"] = "query"
    state["query_text"] = "What is Alpha?"

    result = graph.invoke(state)

    assert result["processing_complete"] is True
    assert "According to node" in result["final_answer"]
