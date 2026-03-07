"""Lightweight performance smoke checks for local API endpoints."""

from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen

BASE = "http://localhost:8000"


def call(method: str, path: str, payload: dict | None = None) -> tuple[float, dict]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = Request(f"{BASE}{path}", method=method, headers=headers, data=body)
    started = time.perf_counter()
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    return elapsed, data


def main() -> None:
    query_elapsed, _ = call("POST", "/query", {"query": "What is this system?"})
    stats_elapsed, _ = call("GET", "/graph/stats")

    print(f"query_elapsed_seconds={query_elapsed:.3f}")
    print(f"stats_elapsed_seconds={stats_elapsed:.3f}")

    if query_elapsed > 5.0:
        raise SystemExit("Query exceeded target budget (5s)")


if __name__ == "__main__":
    main()
