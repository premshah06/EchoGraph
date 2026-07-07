"""Unit tests for KnowledgeStore persistence and retrieval behavior."""

from __future__ import annotations

from datetime import datetime

from backend.knowledge_store import KnowledgeStore, hash_content


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


def test_node_without_derivation_normalizes_to_none(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    node_id = store.add_node(_node_payload("A", "Summary A"))

    node = store.get_node(node_id)

    assert node["derivation"] is None


def test_derivation_round_trips_through_storage(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    payload = _node_payload("Synthesis: A vs B", "Reconciled summary")
    payload["node_type"] = "synthesized"
    payload["derivation"] = {
        "source_node_ids": ["node-a", "node-b"],
        "contradiction_reason": "Claims conflict on the same metric",
        "credibility_assessment": "Source A is more credible",
        "synthesis_reasoning": "Both hold under different conditions",
        "loop_iteration": 1,
    }

    node_id = store.add_node(payload)
    node = store.get_node(node_id)

    assert node["derivation"] == payload["derivation"]


def test_derivation_survives_get_all_nodes(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    payload = _node_payload("Synthesis", "Summary")
    payload["derivation"] = {
        "source_node_ids": ["node-a"],
        "contradiction_reason": "reason",
        "credibility_assessment": "assessment",
        "synthesis_reasoning": "reasoning",
        "loop_iteration": 0,
    }
    store.add_node(payload)

    nodes = store.get_all_nodes()

    assert nodes[0]["derivation"]["source_node_ids"] == ["node-a"]


def test_hash_content_is_deterministic():
    assert hash_content("same text") == hash_content("same text")


def test_hash_content_differs_for_different_text():
    assert hash_content("text A") != hash_content("text B")


def test_find_prior_ingestion_returns_none_when_unseen(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    assert store.find_prior_ingestion(hash_content("never ingested")) is None


def test_record_and_find_prior_ingestion_round_trips(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    content_hash = hash_content("some document text")

    store.record_ingestion(content_hash, {
        "ingestion_id": "abc-123",
        "nodes_created": 5,
        "edges_created": 3,
        "contradictions_resolved": 1,
        "loops_executed": 2,
    })

    prior = store.find_prior_ingestion(content_hash)

    assert prior is not None
    assert prior["ingestion_id"] == "abc-123"
    assert prior["nodes_created"] == 5
    assert prior["edges_created"] == 3
    assert prior["contradictions_resolved"] == 1
    assert prior["loops_executed"] == 2


def test_record_ingestion_upserts_on_same_hash(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    content_hash = hash_content("re-ingested document")

    store.record_ingestion(content_hash, {"ingestion_id": "first", "nodes_created": 1})
    store.record_ingestion(content_hash, {"ingestion_id": "second", "nodes_created": 2})

    prior = store.find_prior_ingestion(content_hash)
    assert prior["ingestion_id"] == "second"
    assert prior["nodes_created"] == 2


def test_reset_clears_ingestion_hashes_too(tmp_path):
    store = KnowledgeStore(persist_directory=str(tmp_path / "db"))
    content_hash = hash_content("doc to be reset away")
    store.record_ingestion(content_hash, {"ingestion_id": "abc"})

    store.reset()

    assert store.find_prior_ingestion(content_hash) is None
