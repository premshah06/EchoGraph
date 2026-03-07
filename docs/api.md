# EchoGraph API Documentation

## Base URL

- `http://localhost:8000`

## REST Endpoints

### `POST /ingest/document`

Ingest plain text content.

Request:

```json
{
  "content": "Document content...",
  "source_label": "notes.txt",
  "events_session": "optional-session-id"
}
```

Response:

```json
{
  "status": "success",
  "ingestion_id": "uuid",
  "events_session": "session-id",
  "nodes_created": 6,
  "edges_created": 8,
  "contradictions_resolved": 1,
  "loops_executed": 1
}
```

### `POST /ingest/url`

Fetch and ingest a web page.

Request:

```json
{
  "url": "https://example.com/article",
  "events_session": "optional-session-id"
}
```

### `POST /query`

Ask a natural-language question.

Request:

```json
{
  "query": "What are the key contradictions?",
  "events_session": "optional-session-id"
}
```

Response:

```json
{
  "answer": "According to node #[...] ...",
  "sources": ["node-id-1", "node-id-2"],
  "retrieved_nodes": [],
  "agent_events": []
}
```

### `GET /graph/nodes`

Returns visualization data.

Response:

```json
{
  "nodes": [],
  "edges": []
}
```

### `GET /graph/stats`

Returns aggregate graph metrics.

Response:

```json
{
  "node_count": 0,
  "edge_count": 0,
  "contradiction_count": 0,
  "synthesized_count": 0,
  "raw_count": 0,
  "bridge_count": 0
}
```

### `DELETE /graph/reset`

Wipes the knowledge base.

Response:

```json
{
  "status": "success",
  "message": "Knowledge base wiped"
}
```

### `GET /health`

Returns service and mode status.

Response:

```json
{
  "status": "healthy",
  "knowledge_store": "connected",
  "graphs": "compiled",
  "demo_mode": false
}
```

## WebSocket

### `WS /stream/{session_id}`

Streams agent events with timestamps.

Event format:

```json
{
  "event": "concept_extracted",
  "agent": "librarian",
  "data": {
    "concept": "...",
    "node_id": "..."
  },
  "timestamp": "2026-03-06T10:00:00+00:00"
}
```

Supported events:

- `agent_start`
- `concept_extracted`
- `connection_found`
- `contradiction_found`
- `resolution_start`
- `resolution_done`
- `loop_back`
- `node_stored`
- `ingestion_complete`
- `scholar_answer`
- `error`

## Error Codes

- `400` invalid user input (URL/content/query issues)
- `403` ingestion blocked in demo mode
- `429` rate limit exceeded
- `500` internal processing failure

## Rate Limits

Applied per client IP and endpoint:

- `/ingest/*`: 30 requests/minute
- `/query`: 60 requests/minute
- `/graph/reset`: 10 requests/minute
- default: 240 requests/minute
