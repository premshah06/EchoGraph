"""End-to-end style validation suite for core product workflows."""

from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as main


class E2EStore:
    def __init__(self):
        self.nodes = []

    def get_all_nodes(self):
        return list(self.nodes)

    def reset(self):
        self.nodes = []


class E2EGraph:
    def __init__(self, kind: str, store: E2EStore):
        self.kind = kind
        self.store = store

    def invoke(self, state):
        if self.kind == "ingest":
            node_id = f"node-{len(self.store.nodes) + 1}"
            self.store.nodes.append(
                {
                    "id": node_id,
                    "concept": "Ingested Concept",
                    "summary": "Ingested summary",
                    "source": state.get("source_label", "doc"),
                    "node_type": "raw",
                    "confidence": 1.0,
                    "contradiction_resolved": False,
                    "connected_to": [],
                    "relationship_types": [],
                    "times_retrieved": 0,
                    "created_at": "",
                }
            )
            callback = state.get("event_callback")
            if callback:
                callback(
                    {
                        "event": "concept_extracted",
                        "data": {"concept": "Ingested Concept", "node_id": node_id},
                        "timestamp": "2026-03-06T00:00:00+00:00",
                    }
                )
            state["new_concepts"] = [{"id": node_id}]
            state["connections"] = []
            state["resolutions"] = [{"id": "res-1"}]
            return state

        state["final_answer"] = "According to node #[node-1], this is supported."
        state["retrieved_nodes"] = self.store.get_all_nodes()[:1]
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
        return FakeAsyncResponse("<html><body><p>E2E URL content</p></body></html>")


def build_e2e_client(monkeypatch):
    store = E2EStore()

    monkeypatch.setattr(main, "KnowledgeStore", lambda persist_directory: store)
    monkeypatch.setattr(main, "create_ingestion_graph", lambda ks: E2EGraph("ingest", store))
    monkeypatch.setattr(main, "create_query_graph", lambda ks: E2EGraph("query", store))
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda follow_redirects=True: FakeAsyncClient())

    return TestClient(main.app)


def test_complete_ingestion_flow_with_real_document(monkeypatch):
    with build_e2e_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={
                "content": "This is an end-to-end ingestion test document.",
                "source_label": "e2e_doc.txt",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"


def test_contradiction_detection_and_resolution_flow(monkeypatch):
    with build_e2e_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        response = client.post(
            "/ingest/document",
            json={"content": "Contradictory claim sample", "source_label": "contradiction.txt"},
        )

    assert response.status_code == 200
    assert response.json()["contradictions_resolved"] >= 0


def test_query_answering_with_citations(monkeypatch):
    with build_e2e_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        client.post(
            "/ingest/document",
            json={"content": "Seed data", "source_label": "seed.txt"},
        )
        query_response = client.post("/query", json={"query": "What is seeded?"})

    assert query_response.status_code == 200
    assert "According to node" in query_response.json()["answer"]


def test_graph_nodes_endpoint_reflects_ingestion_updates(monkeypatch):
    with build_e2e_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        client.post(
            "/ingest/document",
            json={"content": "Graph update sample", "source_label": "graph.txt"},
        )
        graph_response = client.get("/graph/nodes")

    assert graph_response.status_code == 200
    assert len(graph_response.json()["nodes"]) >= 1


def test_websocket_event_streaming(monkeypatch):
    with build_e2e_client(monkeypatch) as client:
        session_id = "e2e-session"
        main.websocket_manager.event_buffer[session_id] = [
            {
                "event": "ingestion_complete",
                "data": {"new_nodes": 1, "edges": 0},
                "timestamp": "2026-03-06T00:00:00+00:00",
            }
        ]

        with client.websocket_connect(f"/stream/{session_id}") as websocket:
            payload = websocket.receive_json()

    assert payload["event"] in {"ingestion_complete", "event_batch", "event_batch_compact"}
