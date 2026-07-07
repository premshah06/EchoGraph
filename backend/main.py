"""
FastAPI main application for EchoGraph.
Provides REST API and WebSocket endpoints for the multi-agent knowledge base.
"""

from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.config import get_settings
from backend.events import build_event
from backend.graphs.ingestion_graph import create_ingestion_graph
from backend.graphs.query_graph import create_query_graph
from backend.knowledge_store import KnowledgeStore, hash_content
from backend.llm_client import get_llm_client
from backend.models import (
    BatchIngestRequest,
    BatchIngestItemResult,
    BatchIngestResponse,
    GraphSearchRequest,
    GraphSearchResponse,
    GraphSearchResult,
    GraphStatsResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    URLIngestRequest,
)

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Inject the active request id (if any) into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON for log-aggregator friendliness."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    """Configure console and rotating file logging once."""
    root = logging.getLogger()
    if root.handlers:
        return

    formatter = JsonFormatter()
    request_id_filter = RequestIdFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_id_filter)

    file_handler = RotatingFileHandler(
        filename="echosystem.log",
        maxBytes=2_000_000,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_id_filter)

    import os
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    root.setLevel(level)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)


configure_logging()
logger = logging.getLogger(__name__)


MAX_DOCUMENT_CHARS = 50_000
MAX_QUERY_CHARS = 2_000
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ConnectionManager:
    """Manage session-scoped websocket connections and event buffering."""

    def __init__(
        self,
        max_buffer_per_session: int = 500,
        flush_interval_ms: int = 40,
        compact_threshold_bytes: int = 4_000,
    ):
        self.active_connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        self.event_buffer: Dict[str, List[dict]] = defaultdict(list)
        self.pending_events: Dict[str, List[dict]] = defaultdict(list)
        self.flush_tasks: Dict[str, asyncio.Task] = {}
        self.max_buffer_per_session = max_buffer_per_session
        self.flush_interval_ms = flush_interval_ms
        self.compact_threshold_bytes = compact_threshold_bytes

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[session_id].add(websocket)
        logger.info("WebSocket connected: session=%s", session_id)

        # Replay buffered events for reconnects or late subscribers.
        buffered = self.event_buffer.get(session_id, [])
        if buffered:
            await self._send_events(websocket, buffered)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        session_connections = self.active_connections.get(session_id)
        if not session_connections:
            return

        session_connections.discard(websocket)
        if not session_connections:
            self.active_connections.pop(session_id, None)
        logger.info("WebSocket disconnected: session=%s", session_id)

    async def send_event(self, session_id: str, event: dict) -> None:
        # Buffer events so clients can reconnect and replay.
        buffer = self.event_buffer[session_id]
        buffer.append(event)
        if len(buffer) > self.max_buffer_per_session:
            del buffer[0 : len(buffer) - self.max_buffer_per_session]

        # Throttle event delivery by batching events in a short flush window.
        self.pending_events[session_id].append(event)
        existing_task = self.flush_tasks.get(session_id)
        if existing_task and not existing_task.done():
            return
        self.flush_tasks[session_id] = asyncio.create_task(self._flush_session(session_id))

    async def _flush_session(self, session_id: str) -> None:
        await asyncio.sleep(self.flush_interval_ms / 1000)

        events = self.pending_events.pop(session_id, [])
        self.flush_tasks.pop(session_id, None)
        if not events:
            return

        session_connections = list(self.active_connections.get(session_id, set()))
        if not session_connections:
            return

        stale: List[WebSocket] = []
        for websocket in session_connections:
            try:
                await self._send_events(websocket, events)
            except Exception:
                stale.append(websocket)

        for websocket in stale:
            self.disconnect(session_id, websocket)

    def _build_payload(self, events: List[dict]) -> dict:
        if len(events) == 1:
            return events[0]

        payload = {
            "event": "event_batch",
            "data": {"events": events, "count": len(events)},
            "timestamp": events[-1].get("timestamp"),
        }

        # Compact large websocket payloads by removing repeated object keys.
        raw_payload_size = len(json.dumps(payload, separators=(",", ":")))
        if raw_payload_size <= self.compact_threshold_bytes:
            return payload

        compact_rows = [
            [event.get("event"), event.get("agent"), event.get("timestamp"), event.get("data", {})]
            for event in events
        ]
        return {
            "event": "event_batch_compact",
            "data": {
                "schema": ["event", "agent", "timestamp", "data"],
                "rows": compact_rows,
                "count": len(events),
            },
            "timestamp": events[-1].get("timestamp"),
        }

    async def _send_events(self, websocket: WebSocket, events: List[dict]) -> None:
        await websocket.send_json(self._build_payload(events))

    async def broadcast(self, event: dict) -> None:
        for session_id in list(self.active_connections.keys()):
            await self.send_event(session_id, event)

    def clear_all(self) -> None:
        for task in self.flush_tasks.values():
            task.cancel()
        self.flush_tasks.clear()
        self.active_connections.clear()
        self.pending_events.clear()
        self.event_buffer.clear()


class InMemoryRateLimiter:
    """Simple in-memory sliding-window limiter for API endpoints."""

    def __init__(self):
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int, int]:
        now = time.time()
        q = self.hits[key]

        while q and q[0] <= now - window_seconds:
            q.popleft()

        if len(q) >= limit:
            retry_after = int(max(1, window_seconds - (now - q[0])))
            return False, 0, retry_after

        q.append(now)
        remaining = max(0, limit - len(q))
        return True, remaining, 0


def sanitize_text(value: str, max_chars: int) -> str:
    """Trim and sanitize user-provided text content."""
    sanitized = CONTROL_CHARS_PATTERN.sub("", value or "")
    sanitized = sanitized.strip()
    return sanitized[:max_chars]


def sanitize_source_label(value: str) -> str:
    """Normalize labels used as source metadata."""
    sanitized = CONTROL_CHARS_PATTERN.sub("", value or "")
    sanitized = " ".join(sanitized.split())
    return sanitized[:200] or "unknown-source"


async def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Validate X-API-Key header against configured keys.

    Auth is disabled (no-op) when no API_KEYS are configured, so local/demo
    deployments keep working without extra setup.
    """
    configured_keys = get_settings().api_keys_set
    if not configured_keys:
        return

    if not x_api_key or x_api_key not in configured_keys:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def build_event_emitter(session_id: str, loop: asyncio.AbstractEventLoop):
    """Create a thread-safe event callback that dispatches to websocket clients."""

    def _emit(event: dict) -> None:
        future = asyncio.run_coroutine_threadsafe(
            websocket_manager.send_event(session_id, event),
            loop,
        )

        def _done_callback(done_future):
            exc = done_future.exception()
            if exc:
                logger.warning("Failed sending websocket event for %s: %s", session_id, exc)

        future.add_done_callback(_done_callback)

    return _emit


knowledge_store: Optional[KnowledgeStore] = None
ingestion_graph = None
query_graph = None
websocket_manager: Optional[ConnectionManager] = None
rate_limiter = InMemoryRateLimiter()
demo_mode_enabled = False


def seed_demo_data_if_needed(store: KnowledgeStore) -> None:
    """Populate deterministic demo knowledge when running without OpenAI keys."""
    existing = store.get_all_nodes()
    if existing:
        return

    llm_client = get_llm_client()
    demo_nodes = [
        {
            "concept": "Foundation Models",
            "summary": "Foundation models are pre-trained on broad data and adapted to downstream tasks.",
            "source": "demo://overview",
            "node_type": "raw",
            "confidence": 0.92,
            "contradiction_resolved": False,
            "connected_to": [],
            "relationship_types": [],
            "times_retrieved": 0,
        },
        {
            "concept": "Retrieval-Augmented Generation",
            "summary": "RAG combines semantic retrieval with generation to ground responses in explicit context.",
            "source": "demo://rag",
            "node_type": "bridge",
            "confidence": 0.9,
            "contradiction_resolved": False,
            "connected_to": [],
            "relationship_types": [],
            "times_retrieved": 0,
        },
        {
            "concept": "Contradiction Synthesis",
            "summary": "Synthesis nodes reconcile conflicting claims by weighing source reliability and context.",
            "source": "demo://synthesis",
            "node_type": "synthesized",
            "confidence": 0.88,
            "contradiction_resolved": True,
            "connected_to": [],
            "relationship_types": [],
            "times_retrieved": 0,
        },
    ]

    stored_ids: List[str] = []
    for node in demo_nodes:
        payload = dict(node)
        payload["embedding"] = llm_client.embed_text(payload["summary"])
        stored_ids.append(store.add_node(payload))

    if len(stored_ids) >= 2:
        store.add_edge(
            {
                "node_a_id": stored_ids[1],
                "node_b_id": stored_ids[0],
                "relationship_type": "is_prerequisite_of",
                "strength": 0.73,
                "explanation": "RAG depends on foundational model capabilities.",
            }
        )
    if len(stored_ids) >= 3:
        store.add_edge(
            {
                "node_a_id": stored_ids[2],
                "node_b_id": stored_ids[1],
                "relationship_type": "extends",
                "strength": 0.77,
                "explanation": "Synthesis extends RAG by resolving conflicting evidence.",
            }
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    global knowledge_store, ingestion_graph, query_graph, websocket_manager, demo_mode_enabled

    settings = get_settings()
    logger.info("Starting EchoGraph backend")

    knowledge_store = KnowledgeStore(persist_directory=settings.chromadb_persist_dir)
    ingestion_graph = create_ingestion_graph(knowledge_store)
    query_graph = create_query_graph(knowledge_store)
    websocket_manager = ConnectionManager()
    demo_mode_enabled = settings.demo_mode or not settings.is_openai_configured

    if demo_mode_enabled:
        logger.warning("Demo mode enabled")
        seed_demo_data_if_needed(knowledge_store)
    else:
        logger.info("OpenAI mode enabled")

    yield

    logger.info("Shutting down EchoGraph backend")


app = FastAPI(
    title="EchoGraph API",
    description="Multi-Agent Living Knowledge Base with Contradiction Resolution",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if True:
    app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    """Apply endpoint rate-limiting and request latency logging."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_var.set(request_id)

    path = request.url.path
    client_host = request.client.host if request.client else "unknown"

    # Endpoint-specific limits.
    limit = 240
    window = 60
    if path.startswith("/ingest"):
        limit = 30
    elif path == "/query":
        limit = 60
    elif path.startswith("/graph/reset"):
        limit = 10

    allowed, remaining, retry_after = rate_limiter.check(
        key=f"{client_host}:{path}",
        limit=limit,
        window_seconds=window,
    )

    try:
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please retry later."},
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": str(retry_after),
                },
            )
            response.headers["X-Request-ID"] = request_id
            return response

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-Request-ID"] = request_id

        logger.info(
            "%s %s -> %s (%.2f ms)",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
        return response
    finally:
        request_id_var.reset(token)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "EchoGraph API", "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health_check():
    """Liveness + dependency health check."""
    store_status = "unavailable"
    if knowledge_store is not None:
        try:
            await run_in_threadpool(knowledge_store.client.heartbeat)
            store_status = "connected"
        except Exception:
            logger.exception("Health check: ChromaDB heartbeat failed")
            store_status = "unreachable"

    graphs_status = "compiled" if (ingestion_graph is not None and query_graph is not None) else "not_compiled"

    from backend.retry import default_circuit_breaker
    circuit_status = default_circuit_breaker.status()

    overall_ok = (
        store_status == "connected"
        and graphs_status == "compiled"
        and circuit_status["state"] != "open"
    )

    return JSONResponse(
        status_code=200 if overall_ok else 503,
        content={
            "status": "healthy" if overall_ok else "degraded",
            "knowledge_store": store_status,
            "graphs": graphs_status,
            "demo_mode": demo_mode_enabled,
            "llm_circuit_breaker": circuit_status,
        },
    )


@app.post("/ingest/document", response_model=IngestResponse, dependencies=[Depends(require_api_key)])
async def ingest_document(request: IngestRequest):
    """Ingest text content into the multi-agent graph pipeline."""
    try:
        if demo_mode_enabled:
            raise HTTPException(
                status_code=403,
                detail="Ingestion is disabled in demo mode. Configure OPENAI_API_KEY to enable it.",
            )

        session_id = request.events_session or str(uuid.uuid4())
        loop = asyncio.get_running_loop()

        content = sanitize_text(request.content, MAX_DOCUMENT_CHARS)
        source_label = sanitize_source_label(request.source_label)
        if not content:
            raise HTTPException(status_code=400, detail="Document content is empty after sanitization")

        content_hash = hash_content(content)
        prior = await run_in_threadpool(knowledge_store.find_prior_ingestion, content_hash)
        if prior is not None:
            logger.info("Ingestion skipped — duplicate content (hash=%s)", content_hash[:12])
            return IngestResponse(
                status="duplicate",
                ingestion_id=prior["ingestion_id"],
                events_session=session_id,
                nodes_created=prior["nodes_created"],
                edges_created=prior["edges_created"],
                contradictions_resolved=prior["contradictions_resolved"],
                loops_executed=prior["loops_executed"],
            )

        initial_state = {
            "input_type": "document",
            "raw_content": content,
            "source_label": source_label,
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
            "session_id": session_id,
            "agent_events": [],
            "event_callback": build_event_emitter(session_id, loop),
        }

        result = await run_in_threadpool(ingestion_graph.invoke, initial_state)

        response = IngestResponse(
            status="success",
            ingestion_id=str(uuid.uuid4()),
            events_session=session_id,
            nodes_created=len(result.get("new_concepts", [])) + len(result.get("resolutions", [])),
            edges_created=len(result.get("connections", [])),
            contradictions_resolved=len(result.get("resolutions", [])),
            loops_executed=int(result.get("loop_count", 0)),
        )

        await run_in_threadpool(
            knowledge_store.record_ingestion, content_hash, response.model_dump()
        )

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error ingesting document")
        raise HTTPException(status_code=500, detail=f"Failed to ingest document: {exc}")


@app.post("/ingest/url", response_model=IngestResponse, dependencies=[Depends(require_api_key)])
async def ingest_url(request: URLIngestRequest):
    """Fetch and ingest web page content by URL."""
    url = str(request.url)
    try:
        if demo_mode_enabled:
            raise HTTPException(
                status_code=403,
                detail="Ingestion is disabled in demo mode. Configure OPENAI_API_KEY to enable it.",
            )

        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup(["script", "style", "noscript"]):
            script.decompose()

        raw_text = "\n".join(
            chunk.strip()
            for chunk in soup.get_text(separator="\n").splitlines()
            if chunk.strip()
        )
        content = sanitize_text(raw_text, MAX_DOCUMENT_CHARS)
        if not content:
            raise HTTPException(status_code=400, detail="No usable text content found at the URL")

        return await ingest_document(
            IngestRequest(
                content=content,
                source_label=url,
                events_session=request.events_session,
            )
        )

    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.exception("Error fetching URL")
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {exc}")
    except Exception as exc:
        logger.exception("Error ingesting URL")
        raise HTTPException(status_code=500, detail=f"Failed to ingest URL: {exc}")


@app.post("/ingest/batch", response_model=BatchIngestResponse, dependencies=[Depends(require_api_key)])
async def ingest_batch(request: BatchIngestRequest):
    """Ingest up to 20 documents sequentially, collecting per-document results."""
    if demo_mode_enabled:
        raise HTTPException(
            status_code=403,
            detail="Ingestion is disabled in demo mode. Configure OPENAI_API_KEY to enable it.",
        )

    session_id = request.events_session or str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    results: list[BatchIngestItemResult] = []
    succeeded = 0
    failed = 0

    for item in request.documents:
        content = sanitize_text(item.content, MAX_DOCUMENT_CHARS)
        source_label = sanitize_source_label(item.source_label)

        if not content:
            results.append(BatchIngestItemResult(
                source_label=item.source_label,
                status="skipped",
                error="Content is empty after sanitization",
            ))
            failed += 1
            continue

        content_hash = hash_content(content)
        prior = await run_in_threadpool(knowledge_store.find_prior_ingestion, content_hash)
        if prior is not None:
            logger.info("Batch item skipped — duplicate content (hash=%s)", content_hash[:12])
            results.append(BatchIngestItemResult(
                source_label=source_label,
                status="duplicate",
                nodes_created=prior["nodes_created"],
                edges_created=prior["edges_created"],
                contradictions_resolved=prior["contradictions_resolved"],
            ))
            succeeded += 1
            continue

        try:
            initial_state = {
                "input_type": "document",
                "raw_content": content,
                "source_label": source_label,
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
                "session_id": session_id,
                "agent_events": [],
                "event_callback": build_event_emitter(session_id, loop),
            }

            result = await run_in_threadpool(ingestion_graph.invoke, initial_state)

            item_result = BatchIngestItemResult(
                source_label=source_label,
                status="success",
                nodes_created=len(result.get("new_concepts", [])) + len(result.get("resolutions", [])),
                edges_created=len(result.get("connections", [])),
                contradictions_resolved=len(result.get("resolutions", [])),
            )
            results.append(item_result)
            succeeded += 1

            await run_in_threadpool(
                knowledge_store.record_ingestion,
                content_hash,
                {
                    "ingestion_id": str(uuid.uuid4()),
                    "nodes_created": item_result.nodes_created,
                    "edges_created": item_result.edges_created,
                    "contradictions_resolved": item_result.contradictions_resolved,
                    "loops_executed": int(result.get("loop_count", 0)),
                },
            )

        except Exception as exc:
            logger.exception("Batch ingest failed for source=%s", source_label)
            results.append(BatchIngestItemResult(
                source_label=source_label,
                status="failed",
                error=str(exc)[:300],
            ))
            failed += 1

    return BatchIngestResponse(
        status="complete" if failed == 0 else "partial",
        total=len(request.documents),
        succeeded=succeeded,
        failed=failed,
        events_session=session_id,
        results=results,
    )


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
async def query_knowledge(request: QueryRequest):
    """Run query graph and return grounded answer with source IDs."""
    try:
        session_id = request.events_session or str(uuid.uuid4())
        loop = asyncio.get_running_loop()

        query = sanitize_text(request.query, MAX_QUERY_CHARS)
        if not query:
            raise HTTPException(status_code=400, detail="Query is empty after sanitization")

        initial_state = {
            "input_type": "query",
            "raw_content": "",
            "source_label": "",
            "existing_nodes": [],
            "new_concepts": [],
            "contradictions": [],
            "connections": [],
            "resolutions": [],
            "query_text": query,
            "retrieved_nodes": [],
            "final_answer": "",
            "current_agent": "",
            "processing_complete": False,
            "contradiction_found": False,
            "resolution_confidence": 1.0,
            "loop_count": 0,
            "session_id": session_id,
            "agent_events": [],
            "event_callback": build_event_emitter(session_id, loop),
        }

        result = await run_in_threadpool(query_graph.invoke, initial_state)

        return QueryResponse(
            answer=result.get("final_answer", "No answer generated"),
            sources=[node["id"] for node in result.get("retrieved_nodes", [])],
            retrieved_nodes=result.get("retrieved_nodes", []),
            agent_events=result.get("agent_events", []),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error processing query")
        raise HTTPException(status_code=500, detail=f"Failed to process query: {exc}")


@app.get("/graph/nodes", dependencies=[Depends(require_api_key)])
async def get_graph_data():
    """Return all nodes and edges for visualization."""
    try:
        nodes = knowledge_store.get_all_nodes()
        edges = []

        for node in nodes:
            connected_to = node.get("connected_to", [])
            relationship_types = node.get("relationship_types", [])
            edge_strengths = node.get("edge_strengths", [])
            for idx, target_id in enumerate(connected_to):
                if not target_id:
                    continue
                edges.append({
                    "source": node["id"],
                    "target": target_id,
                    "type": relationship_types[idx] if idx < len(relationship_types) else "related",
                    "strength": edge_strengths[idx] if idx < len(edge_strengths) else 1.0,
                })

        return {"nodes": nodes, "edges": edges}
    except Exception as exc:
        logger.exception("Error getting graph data")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve graph data: {exc}")


@app.get("/graph/nodes/{node_id}/provenance", dependencies=[Depends(require_api_key)])
async def get_node_provenance(node_id: str):
    """
    Return the full derivation chain for a node — why the graph believes it.

    For a synthesized node, walks backwards through its `derivation` record
    (source node IDs, the contradiction Critic flagged, Synthesizer's
    reasoning, and which resolution loop iteration produced it), recursing
    into any source node that is itself synthesized. Raw/bridge nodes have no
    derivation and are returned as a single-node chain with derivation=None.
    """
    try:
        node = knowledge_store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        visited: set = set()

        def build_trace(current_id: str, depth: int = 0) -> Optional[Dict[str, Any]]:
            if current_id in visited or depth > 10:
                return None
            visited.add(current_id)

            current = knowledge_store.get_node(current_id)
            if current is None:
                return {
                    "id": current_id,
                    "concept": None,
                    "node_type": None,
                    "found": False,
                    "derivation": None,
                    "sources": [],
                }

            derivation = current.get("derivation")
            sources = []
            if derivation:
                for source_id in derivation.get("source_node_ids", []):
                    child = build_trace(source_id, depth + 1)
                    if child:
                        sources.append(child)

            return {
                "id": current["id"],
                "concept": current["concept"],
                "node_type": current["node_type"],
                "found": True,
                "derivation": derivation,
                "sources": sources,
            }

        trace = build_trace(node_id)
        return {"node_id": node_id, "trace": trace}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error building provenance trace for node %s", node_id)
        raise HTTPException(status_code=500, detail=f"Failed to build provenance trace: {exc}")


@app.post("/audit/confidence", dependencies=[Depends(require_api_key)])
async def audit_confidence(sample_size: Optional[int] = None):
    """
    Independently judge whether Synthesizer's self-reported confidence on
    already-synthesized nodes is well-calibrated. Re-derives each
    contradiction from the node's stored provenance and asks a fresh LLM call
    to score it blind, then reports the gap against Synthesizer's original
    confidence.

    Not part of the ingestion hot path — run on-demand (e.g. periodically, or
    before reviewing what the graph has concluded). Costs one extra LLM call
    per synthesized node audited; pass sample_size to cap it.
    """
    if demo_mode_enabled:
        raise HTTPException(
            status_code=403,
            detail="Confidence auditing requires a real LLM and is disabled in demo mode.",
        )

    try:
        from backend.audit.confidence_auditor import run_audit

        summary = await run_in_threadpool(run_audit, knowledge_store, sample_size)
        return summary
    except Exception as exc:
        logger.exception("Error running confidence audit")
        raise HTTPException(status_code=500, detail=f"Failed to run confidence audit: {exc}")


@app.post("/graph/search", response_model=GraphSearchResponse, dependencies=[Depends(require_api_key)])
async def search_graph(request: GraphSearchRequest):
    """Semantic search over knowledge graph nodes using embedding similarity."""
    try:
        query = sanitize_text(request.query, MAX_QUERY_CHARS)
        if not query:
            raise HTTPException(status_code=400, detail="Query is empty after sanitization")

        llm_client = get_llm_client()
        query_embedding = await run_in_threadpool(llm_client.embed_text, query)

        raw_results = await run_in_threadpool(
            knowledge_store.search_similar,
            query_embedding,
            request.top_k,
            request.threshold,
        )

        # Optional node_type filter
        if request.node_types:
            allowed = set(request.node_types)
            raw_results = [n for n in raw_results if n.get("node_type") in allowed]

        results = [
            GraphSearchResult(
                id=n["id"],
                concept=n["concept"],
                summary=n["summary"],
                source=n.get("source", ""),
                node_type=n.get("node_type", "raw"),
                confidence=float(n.get("confidence", 1.0)),
                similarity=round(float(n.get("similarity", 0.0)), 4),
                connected_to=n.get("connected_to", []),
            )
            for n in raw_results
        ]

        return GraphSearchResponse(query=query, results=results, total=len(results))

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error searching graph")
        raise HTTPException(status_code=500, detail=f"Failed to search graph: {exc}")


@app.get("/graph/stats", response_model=GraphStatsResponse, dependencies=[Depends(require_api_key)])
async def get_graph_stats():
    """Return current graph statistics."""
    try:
        nodes = knowledge_store.get_all_nodes()

        synthesized_count = sum(1 for n in nodes if n["node_type"] == "synthesized")
        raw_count = sum(1 for n in nodes if n["node_type"] == "raw")
        bridge_count = sum(1 for n in nodes if n["node_type"] == "bridge")
        contradiction_count = sum(1 for n in nodes if n.get("contradiction_resolved", False))
        edge_count = sum(len(n.get("connected_to", [])) for n in nodes)

        # Per-source breakdown: node count + average confidence, sorted by node count desc.
        source_buckets: dict = {}
        for n in nodes:
            src = n.get("source") or "unknown"
            bucket = source_buckets.setdefault(src, {"count": 0, "conf_sum": 0.0})
            bucket["count"] += 1
            bucket["conf_sum"] += float(n.get("confidence", 1.0))

        from backend.models import SourceStat
        sources = sorted(
            [
                SourceStat(
                    source=src,
                    node_count=b["count"],
                    avg_confidence=round(b["conf_sum"] / b["count"], 3),
                )
                for src, b in source_buckets.items()
            ],
            key=lambda s: s.node_count,
            reverse=True,
        )

        optimization_summary = None
        llm_client = get_llm_client()
        if hasattr(llm_client, "session"):
            optimization_summary = llm_client.session.summary()

        return GraphStatsResponse(
            node_count=len(nodes),
            edge_count=edge_count,
            contradiction_count=contradiction_count,
            synthesized_count=synthesized_count,
            raw_count=raw_count,
            bridge_count=bridge_count,
            sources=sources,
            optimization=optimization_summary,
        )
    except Exception as exc:
        logger.exception("Error getting graph stats")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve graph stats: {exc}")


@app.get("/graph/export", dependencies=[Depends(require_api_key)])
async def export_graph():
    """Export the full knowledge graph as a downloadable JSON file."""
    try:
        from datetime import datetime, timezone
        nodes = knowledge_store.get_all_nodes()
        edges = []

        for node in nodes:
            connected_to = node.get("connected_to", [])
            relationship_types = node.get("relationship_types", [])
            edge_strengths = node.get("edge_strengths", [])
            for idx, target_id in enumerate(connected_to):
                if not target_id:
                    continue
                edges.append({
                    "source": node["id"],
                    "target": target_id,
                    "relationship": relationship_types[idx] if idx < len(relationship_types) else "related",
                    "strength": edge_strengths[idx] if idx < len(edge_strengths) else 1.0,
                })

        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

        return JSONResponse(
            content=payload,
            headers={"Content-Disposition": 'attachment; filename="echograph-export.json"'},
        )
    except Exception as exc:
        logger.exception("Error exporting graph")
        raise HTTPException(status_code=500, detail=f"Failed to export graph: {exc}")


@app.delete("/graph/nodes/{node_id}", dependencies=[Depends(require_api_key)])
async def delete_node(node_id: str):
    """Delete a single node and clean up its edges."""
    try:
        deleted = await run_in_threadpool(knowledge_store.delete_node, node_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        logger.info("Deleted node %s", node_id)
        return {"status": "success", "deleted_id": node_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error deleting node %s", node_id)
        raise HTTPException(status_code=500, detail=f"Failed to delete node: {exc}")


@app.delete("/graph/reset", dependencies=[Depends(require_api_key)])
async def reset_graph():
    """Wipe the knowledge base."""
    try:
        knowledge_store.reset()
        websocket_manager.clear_all()
        return {"status": "success", "message": "Knowledge base wiped"}
    except Exception as exc:
        logger.exception("Error resetting graph")
        raise HTTPException(status_code=500, detail=f"Failed to reset graph: {exc}")


@app.websocket("/stream/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for agent event streaming."""
    await websocket_manager.connect(session_id, websocket)

    try:
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() == "ping":
                await websocket.send_json(build_event("pong", {"session_id": session_id}, agent="system"))
    except WebSocketDisconnect:
        websocket_manager.disconnect(session_id, websocket)
    except Exception:
        websocket_manager.disconnect(session_id, websocket)
        logger.exception("WebSocket error for session %s", session_id)
