"""Performance-oriented checks for API response budgets and concurrency behavior."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import backend.main as main


class PerfStore:
    def get_all_nodes(self):
        return [
            {
                "id": "node-1",
                "concept": "Perf Node",
                "summary": "Performance test node",
                "source": "perf",
                "node_type": "raw",
                "confidence": 1.0,
                "contradiction_resolved": False,
                "connected_to": [],
                "relationship_types": [],
                "times_retrieved": 0,
                "created_at": "",
            }
        ]

    def reset(self):
        return None


class PerfGraph:
    def __init__(self, kind: str):
        self.kind = kind

    def invoke(self, state):
        if self.kind == "ingest":
            state["new_concepts"] = [{"id": "node-1"}]
            state["connections"] = []
            state["resolutions"] = []
            return state
        state["final_answer"] = "According to node #[node-1], performance budget passed."
        state["retrieved_nodes"] = [{"id": "node-1"}]
        return state


def build_perf_client(monkeypatch):
    monkeypatch.setattr(main, "KnowledgeStore", lambda persist_directory: PerfStore())
    monkeypatch.setattr(main, "create_ingestion_graph", lambda store: PerfGraph("ingest"))
    monkeypatch.setattr(main, "create_query_graph", lambda store: PerfGraph("query"))
    return TestClient(main.app)


def test_query_response_time_budget(monkeypatch):
    with build_perf_client(monkeypatch) as client:
        started = time.perf_counter()
        response = client.post("/query", json={"query": "performance check"})
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 5.0


def test_ingestion_response_time_budget(monkeypatch):
    with build_perf_client(monkeypatch) as client:
        main.demo_mode_enabled = False
        started = time.perf_counter()
        response = client.post(
            "/ingest/document",
            json={"content": "perf content", "source_label": "perf.txt"},
        )
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 30.0


def test_concurrent_query_requests(monkeypatch):
    with build_perf_client(monkeypatch) as client:
        def _do_query(_index: int):
            response = client.post("/query", json={"query": "parallel perf check"})
            return response.status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(_do_query, range(20)))

    assert all(status == 200 for status in statuses)
