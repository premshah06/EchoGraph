"""Unit tests for KnowledgeStore persistence and retrieval behavior."""

from __future__ import annotations

from datetime import datetime

from backend.knowledge_store import KnowledgeStore


def _node_payload(concept: str, summary: str):
    return {
        "concept": concept,
        "summary": summary,
        "source": "unit-test.txt",
        "node_type": "raw",
        "confidence": 1.0,
        "contradiction_resolved": False,
        "connected_to": [],
        "relationship_types": [],
        "embedding": [0.2] * 1536,
        "created_at": datetime.utcnow().isoformat(),
        "times_retrieved": 0,
    }


def test_add_and_get_node(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    node_id = store.add_node(_node_payload("A", "Summary A"))

    node = store.get_node(node_id)

    assert node is not None
    assert node["concept"] == "A"


def test_search_similar_respects_threshold(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    store.add_node(_node_payload("A", "Summary A"))

    results = store.search_similar([0.2] * 1536, top_k=3, threshold=0.0)

    assert len(results) >= 1
    assert "similarity" in results[0]


def test_update_retrieval_count(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    node_id = store.add_node(_node_payload("A", "Summary A"))

    store.update_retrieval_count(node_id)
    store.update_retrieval_count(node_id)

    node = store.get_node(node_id)
    assert node["times_retrieved"] == 2


def test_reset_clears_collection(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    store.add_node(_node_payload("A", "Summary A"))

    store.reset()

    assert store.get_all_nodes() == []
