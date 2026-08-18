"""Integration-style API contract tests with faked backend collaborators."""

from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as main


class FakeChromaClient:
    def heartbeat(self):
        return 1


class FakeStore:
    def __init__(self):
        self.client = FakeChromaClient()
        self.ingestion_hashes_by_key = {}
        self.nodes = [
            {
                "id": "node-1",
                "concept": "A",
                "summary": "Summary A",
                "source": "source-a",
                "node_type": "raw",
                "confidence": 1.0,
                "contradiction_resolved": False,
                "connected_to": ["node-2"],
                "relationship_types": ["supports"],
                "times_retrieved": 0,
                "created_at": "",
                "derivation": None,
            },
            {
                "id": "node-2",
                "concept": "B",
                "summary": "Summary B",
                "source": "source-b",
                "node_type": "synthesized",
                "confidence": 0.9,
                "contradiction_resolved": True,
                "connected_to": [],
                "relationship_types": [],
                "times_retrieved": 0,
                "created_at": "",
                "derivation": {
                    "source_node_ids": ["node-1"],
                    "contradiction_reason": "Claims conflict on the same metric",
                    "credibility_assessment": "Source A is more credible",
                    "synthesis_reasoning": "Both hold under different conditions",
                    "loop_iteration": 0,
                },
            },
        ]

    def get_all_nodes(self):
        return list(self.nodes)

    def get_node(self, node_id: str, include_embedding: bool = False):
        for node in self.nodes:
            if node["id"] == node_id:
                return dict(node)
        return None

    def reset(self):
        self.nodes = []

    def delete_node(self, node_id: str) -> bool:
        before = len(self.nodes)
        self.nodes = [n for n in self.nodes if n["id"] != node_id]
        return len(self.nodes) < before

    def find_prior_ingestion(self, content_hash: str):
        return self.ingestion_hashes_by_key.get(content_hash)

    def record_ingestion(self, content_hash: str, result: dict):
        self.ingestion_hashes_by_key[content_hash] = {
            "ingestion_id": result.get("ingestion_id", ""),
            "nodes_created": result.get("nodes_created", 0),
            "edges_created": result.get("edges_created", 0),
            "contradictions_resolved": result.get("contradictions_resolved", 0),
            "loops_executed": result.get("loops_executed", 0),
            "ingested_at": "",
        }


class FakeGraph:
    def __init__(self, kind: str):
        self.kind = kind

    def invoke(self, state):
        if self.kind == "ingest":
            state["new_concepts"] = [{"id": "temp-1"}]
            state["connections"] = [{"source": "node-1", "target": "node-2"}]
            state["resolutions"] = []
            return state

        state["final_answer"] = "According to node #[node-1], answer."
        state["retrieved_nodes"] = [
            {
                "id": "node-1",
                "concept": "A",
                "summary": "Summary A",
                "source": "source-a",
                "node_type": "raw",
                "times_retrieved": 1,
            }
        ]
        state["agent_events"] = [
            {
                "event": "scholar_answer",
                "data": {"answer": state["final_answer"], "sources": ["node-1"]},
                "timestamp": "2026-03-06T00:00:00+00:00",
            }
        ]
        return state


class FakeAsyncResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, _url, timeout=30.0):
        return FakeAsyncResponse("<html><body><p>URL Content</p></body></html>")


def build_client(monkeypatch):
    fake_store = FakeStore()

    monkeypatch.setattr(main, "KnowledgeStore", lambda persist_directory: fake_store)
    monkeypatch.setattr(main, "create_ingestion_graph", lambda store: FakeGraph("ingest"))
    monkeypatch.setattr(main, "create_query_graph", lambda store: FakeGraph("query"))
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda follow_redirects=True: FakeAsyncClient())

    client = TestClient(main.app)
    return client


def test_ingest_document_endpoint(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={"content": "hello", "source_label": "doc.txt"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["nodes_created"] >= 1


def test_ingest_document_endpoint_detects_duplicate(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        first = client.post(
            "/ingest/document",
            json={"content": "duplicate test content", "source_label": "doc.txt"},
        )
        second = client.post(
            "/ingest/document",
            json={"content": "duplicate test content", "source_label": "doc.txt"},
        )

    assert first.status_code == 200
    assert first.json()["status"] == "success"

    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["status"] == "duplicate"
    assert second_payload["ingestion_id"] == first.json()["ingestion_id"]


def test_ingest_document_endpoint_treats_different_content_as_new(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        first = client.post(
            "/ingest/document",
            json={"content": "content A", "source_label": "doc.txt"},
        )
        second = client.post(
            "/ingest/document",
            json={"content": "content B", "source_label": "doc.txt"},
        )

    assert first.json()["status"] == "success"
    assert second.json()["status"] == "success"


def test_ingest_url_endpoint(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_query_endpoint(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.post("/query", json={"query": "What is A?"})

    assert response.status_code == 200
    payload = response.json()
    assert "According to node" in payload["answer"]
    assert payload["sources"] == ["node-1"]


def test_graph_endpoints(monkeypatch):
    with build_client(monkeypatch) as client:
        nodes_response = client.get("/graph/nodes")
        stats_response = client.get("/graph/stats")

    assert nodes_response.status_code == 200
    assert stats_response.status_code == 200
    assert len(nodes_response.json()["nodes"]) == 2
    assert stats_response.json()["node_count"] == 2


def test_export_returns_nodes_and_edges(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/graph/export")

    assert response.status_code == 200
    payload = response.json()
    assert "nodes" in payload
    assert "edges" in payload
    assert payload["node_count"] == 2
    assert payload["edge_count"] >= 1
    assert "exported_at" in payload


def test_export_content_disposition_header(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/graph/export")

    assert "attachment" in response.headers.get("content-disposition", "")
    assert "graphmediator-export.json" in response.headers.get("content-disposition", "")


def test_delete_node_endpoint(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.delete("/graph/nodes/node-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["deleted_id"] == "node-1"


def test_delete_node_returns_404_for_unknown_id(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.delete("/graph/nodes/does-not-exist")

    assert response.status_code == 404


def test_provenance_endpoint_returns_derivation_chain(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/graph/nodes/node-2/provenance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "node-2"

    trace = payload["trace"]
    assert trace["id"] == "node-2"
    assert trace["derivation"]["contradiction_reason"] == "Claims conflict on the same metric"
    assert len(trace["sources"]) == 1
    assert trace["sources"][0]["id"] == "node-1"
    assert trace["sources"][0]["derivation"] is None
    assert trace["sources"][0]["sources"] == []


def test_provenance_endpoint_handles_raw_node_with_no_derivation(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/graph/nodes/node-1/provenance")

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace["id"] == "node-1"
    assert trace["derivation"] is None
    assert trace["sources"] == []


def test_provenance_endpoint_returns_404_for_unknown_node(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/graph/nodes/does-not-exist/provenance")

    assert response.status_code == 404


def test_audit_confidence_endpoint_returns_summary(monkeypatch):
    class FakeJudgeLLM:
        def invoke(self, _prompt: str) -> str:
            return "CONFIDENCE: 0.4\nREASONING: The evidence is weaker than claimed."

    monkeypatch.setattr(
        "backend.audit.confidence_auditor.get_llm_client",
        lambda: FakeJudgeLLM(),
    )

    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post("/audit/confidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["audited_count"] == 1  # only node-2 has a derivation
    assert payload["results"][0]["node_id"] == "node-2"
    assert payload["results"][0]["direction"] == "overconfident"


def test_audit_confidence_endpoint_disabled_in_demo_mode(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = True
        response = client.post("/audit/confidence")

    assert response.status_code == 403


def test_audit_confidence_endpoint_respects_sample_size(monkeypatch):
    class FakeJudgeLLM:
        def invoke(self, _prompt: str) -> str:
            return "CONFIDENCE: 0.9\nREASONING: ok"

    monkeypatch.setattr(
        "backend.audit.confidence_auditor.get_llm_client",
        lambda: FakeJudgeLLM(),
    )

    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post("/audit/confidence?sample_size=0")

    assert response.status_code == 200
    assert response.json()["audited_count"] == 0


def test_reset_endpoint(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.delete("/graph/reset")

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_protected_endpoints_open_when_no_api_keys_configured(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "")
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={"content": "hello", "source_label": "doc.txt"},
        )

    assert response.status_code == 200


def test_protected_endpoint_rejects_missing_key_when_configured(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={"content": "hello", "source_label": "doc.txt"},
        )

    assert response.status_code == 401


def test_protected_endpoint_rejects_wrong_key_when_configured(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={"content": "hello", "source_label": "doc.txt"},
            headers={"X-API-Key": "wrong-key"},
        )

    assert response.status_code == 401


def test_protected_endpoint_accepts_correct_key_when_configured(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={"content": "hello", "source_label": "doc.txt"},
            headers={"X-API-Key": "secret-key"},
        )

    assert response.status_code == 200


def test_query_endpoint_protected_by_api_key(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        unauthenticated = client.post("/query", json={"query": "What is A?"})
        authenticated = client.post(
            "/query",
            json={"query": "What is A?"},
            headers={"X-API-Key": "secret-key"},
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


def test_reset_endpoint_protected_by_api_key(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        unauthenticated = client.delete("/graph/reset")
        authenticated = client.delete(
            "/graph/reset", headers={"X-API-Key": "secret-key"}
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


def test_graph_nodes_protected_by_api_key(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        unauthenticated = client.get("/graph/nodes")
        authenticated = client.get("/graph/nodes", headers={"X-API-Key": "secret-key"})

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


def test_graph_stats_protected_by_api_key(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        unauthenticated = client.get("/graph/stats")
        authenticated = client.get("/graph/stats", headers={"X-API-Key": "secret-key"})

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


def test_graph_export_protected_by_api_key(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        unauthenticated = client.get("/graph/export")
        authenticated = client.get("/graph/export", headers={"X-API-Key": "secret-key"})

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


def test_invalid_inputs(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.post("/query", json={"query": ""})

    # Pydantic validation should reject empty query strings.
    assert response.status_code == 422


def test_health_check_reports_healthy_when_store_reachable(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["knowledge_store"] == "connected"
    assert payload["graphs"] == "compiled"


def test_health_check_reports_degraded_when_store_unreachable(monkeypatch):
    with build_client(monkeypatch) as client:
        def broken_heartbeat():
            raise RuntimeError("chromadb down")

        main.knowledge_store.client.heartbeat = broken_heartbeat
        response = client.get("/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["knowledge_store"] == "unreachable"


def test_health_check_reports_circuit_breaker_status(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/health")

    payload = response.json()
    assert "llm_circuit_breaker" in payload
    assert payload["llm_circuit_breaker"]["state"] == "closed"


def test_health_check_degrades_when_circuit_breaker_open(monkeypatch):
    from backend.retry import default_circuit_breaker

    original_state = (default_circuit_breaker._consecutive_failures, default_circuit_breaker._opened_at)
    try:
        for _ in range(default_circuit_breaker.failure_threshold):
            default_circuit_breaker.record_failure()

        with build_client(monkeypatch) as client:
            response = client.get("/health")

        assert response.status_code == 503
        payload = response.json()
        assert payload["status"] == "degraded"
        assert payload["llm_circuit_breaker"]["state"] == "open"
    finally:
        default_circuit_breaker._consecutive_failures, default_circuit_breaker._opened_at = original_state


def test_response_includes_request_id_header(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


def test_request_id_header_is_echoed_when_provided(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/health", headers={"X-Request-ID": "client-supplied-id"})

    assert response.headers["x-request-id"] == "client-supplied-id"


def test_batch_ingest_succeeds(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/batch",
            json={
                "documents": [
                    {"content": "First document content.", "source_label": "doc-1.txt"},
                    {"content": "Second document content.", "source_label": "doc-2.txt"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["succeeded"] == 2
    assert payload["failed"] == 0
    assert payload["status"] == "complete"
    assert len(payload["results"]) == 2
    assert all(r["status"] == "success" for r in payload["results"])


def test_batch_ingest_partial_on_empty_content(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/batch",
            json={
                "documents": [
                    {"content": "Valid content here.", "source_label": "doc-1.txt"},
                    {"content": "   ", "source_label": "doc-2.txt"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["succeeded"] == 1
    assert payload["failed"] == 1
    assert payload["status"] == "partial"
    statuses = {r["source_label"]: r["status"] for r in payload["results"]}
    assert statuses["doc-1.txt"] == "success"
    assert statuses["doc-2.txt"] == "skipped"


def test_batch_ingest_detects_duplicate_within_batch(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/batch",
            json={
                "documents": [
                    {"content": "repeated content", "source_label": "doc-1.txt"},
                    {"content": "repeated content", "source_label": "doc-2.txt"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] == 2
    assert payload["failed"] == 0
    statuses = {r["source_label"]: r["status"] for r in payload["results"]}
    assert statuses["doc-1.txt"] == "success"
    assert statuses["doc-2.txt"] == "duplicate"


def test_batch_ingest_detects_duplicate_against_prior_ingestion(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        client.post(
            "/ingest/document",
            json={"content": "already ingested earlier", "source_label": "doc-0.txt"},
        )
        response = client.post(
            "/ingest/batch",
            json={"documents": [{"content": "already ingested earlier", "source_label": "doc-1.txt"}]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["status"] == "duplicate"


def test_batch_ingest_rejects_more_than_20_docs(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/batch",
            json={
                "documents": [
                    {"content": f"Content {i}", "source_label": f"doc-{i}.txt"}
                    for i in range(21)
                ]
            },
        )

    assert response.status_code == 422


def test_batch_ingest_blocked_in_demo_mode(monkeypatch):
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = True
        response = client.post(
            "/ingest/batch",
            json={"documents": [{"content": "hello", "source_label": "doc.txt"}]},
        )

    assert response.status_code == 403


def test_batch_ingest_protected_by_api_key(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "api_keys", "secret-key")
    with build_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        unauth = client.post(
            "/ingest/batch",
            json={"documents": [{"content": "hello", "source_label": "doc.txt"}]},
        )
        auth = client.post(
            "/ingest/batch",
            json={"documents": [{"content": "hello", "source_label": "doc.txt"}]},
            headers={"X-API-Key": "secret-key"},
        )

    assert unauth.status_code == 401
    assert auth.status_code == 200


def test_websocket_connect_and_replay(monkeypatch):
    with build_client(monkeypatch) as client:
        main.websocket_manager.event_buffer["abc123"] = [
            {
                "event": "agent_start",
                "data": {"label": "test"},
                "timestamp": "2026-03-06T00:00:00+00:00",
            }
        ]

        with client.websocket_connect("/stream/abc123") as websocket:
            replayed = websocket.receive_json()
            assert replayed["event"] == "agent_start"

            websocket.send_text("ping")
            pong = websocket.receive_json()
            assert pong["event"] == "pong"
