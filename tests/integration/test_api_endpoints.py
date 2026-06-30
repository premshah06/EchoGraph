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
            },
        ]

    def get_all_nodes(self):
        return list(self.nodes)

    def reset(self):
        self.nodes = []


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
    assert "echograph-export.json" in response.headers.get("content-disposition", "")


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


def test_response_includes_request_id_header(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


def test_request_id_header_is_echoed_when_provided(monkeypatch):
    with build_client(monkeypatch) as client:
        response = client.get("/health", headers={"X-Request-ID": "client-supplied-id"})

    assert response.headers["x-request-id"] == "client-supplied-id"


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
