# EchoGraph Developer Guide

## Project Layout

- `backend/main.py`: FastAPI app, middleware, endpoint orchestration, WebSocket manager
- `backend/graphs/`: LangGraph workflows for ingestion and query paths
- `backend/agents/`: Agent implementations
- `backend/knowledge_store.py`: ChromaDB persistence abstraction
- `backend/events.py`: Structured event emission helpers
- `frontend/`: Static UI (HTML/CSS/JS)
- `tests/`: Unit and integration tests

## Agent Architecture

### Librarian

- Extracts concepts from content
- Generates embeddings
- Checks overlap against existing nodes

### Philosopher

- Identifies semantic relationships
- Classifies relationship type and strength

### Critic

- Detects contradictions for semantically related claims
- Captures reasoning and credibility context

### Synthesizer

- Creates synthesized resolutions for contradictions
- Computes confidence and can trigger loop-back

### Scholar

- Retrieves relevant nodes for queries
- Prioritizes `synthesized > bridge > raw`
- Produces cited answers

## LangGraph Flow

### Ingestion Graph

`Librarian -> Philosopher -> Critic -> (Synthesizer if contradiction) -> Store`

- Critic condition:
  - `contradiction_found = true` routes to Synthesizer
  - otherwise routes directly to Store
- Synthesizer condition:
  - loops back to Critic when confidence `< 0.6` and loop count `< 3`

### Query Graph

`Scholar -> END`

## State Management

Shared `EchoState` carries:

- input payload (`input_type`, `raw_content`, `source_label`, `query_text`)
- intermediate data (`new_concepts`, `connections`, `contradictions`, `resolutions`)
- output data (`retrieved_nodes`, `final_answer`)
- control data (`loop_count`, `resolution_confidence`, `processing_complete`)
- streaming data (`session_id`, `agent_events`, `event_callback`)

## Frontend Notes

- Uses native module imports for Three.js, OrbitControls, d3-force-3d, and PDF.js
- Graph rendering handled by `KnowledgeGraph3D` class
- WebSocket integration handled by `SessionSocket` with auto-reconnect

## Troubleshooting

### Backend starts but ingestion fails with 403

- You are in demo mode (`OPENAI_API_KEY` missing).
- Configure API key or disable demo mode.

### WebSocket events not appearing

- Ensure frontend passes `events_session` in request body.
- Verify socket connects to `/stream/{session_id}` before request execution.

### Empty graph

- Check `/health` for demo mode and run `/graph/nodes` to confirm data availability.
- In demo mode, sample nodes should be auto-seeded on startup.

### PDF ingestion appears empty

- Validate that PDF has selectable text (not image-only scans).
