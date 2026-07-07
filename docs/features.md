# Feature Backlog — Flagship & Production Features

Curated backlog of high-end, production-level features for EchoGraph, chosen to
maximize both resume value and demonstrated depth in agentic engineering. Each
feature notes **what it is**, **why it's flagship-worthy**, and **where it hooks
into the existing codebase** so we can pick any one up later without re-deriving
the design.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Tier 1 — Agentic Depth ("I deeply understand agents")

### 1. Agent Evaluation Harness ("EvalGraph")  `[x]`
**What:** A golden-dataset test suite that runs curated documents through the
ingestion pipeline and scores each agent's output — Librarian concept-extraction
recall, Critic contradiction precision/recall (against labeled contradiction
pairs), Synthesizer confidence calibration. Emits a scorecard + regression diff
on every run.
**Why flagship:** Evals are the #1 signal of production agentic work vs. hobby
demos. "I built the agents" is common; "I built the harness that proves the
agents are correct and catches regressions" is senior-level.
**Hooks into:** `backend/graphs/ingestion_graph.py` (run pipeline headless), the
`agent_events` already captured in `EchoState`. Golden set as JSON fixtures.

**Status (2026-07-06):** Done. Built `backend/eval/` — `fixtures.py` (golden
case schema: documents, expected concept keywords, expected contradiction +
confidence range, optional query/citation check), `runner.py` (invokes the
real `create_ingestion_graph`/`create_query_graph` headlessly against a
throwaway `KnowledgeStore`, same code path as the FastAPI endpoints),
`scorer.py` (grades results — substring concept-recall, contradiction
detection match, confidence-in-range, citation presence), and `run.py` (CLI:
`python -m backend.eval.run`, prints a scorecard). Two golden fixtures ship:
a genuine drug-trial contradiction case and a non-contradiction case (related
topic, non-conflicting claims) to check Critic isn't trigger-happy.

Runs are labeled either "smoke test only" (demo mode / no API key — validates
wiring, not reasoning) or "evaluated" (real OpenAI key — validates actual
reasoning quality); only real-eval failures fail the CLI's exit code.

**Found on first run:** `DemoLLMClient.invoke`'s concept-extraction fallback
(`backend/llm_client.py`) parses the *prompt's own instructional text* instead
of returning a properly-shaped fake response, when the prompt is realistic
multi-line text (as all real agent calls send) rather than a single line. This
is a pre-existing bug, not introduced by this work — flagged, not fixed, since
it's outside today's scope. This is exactly the kind of regression an eval
harness is supposed to catch, and a good, honest anecdote for the "why evals
matter" story: the harness caught a bug within minutes of its first run.

New test coverage: `tests/unit/test_eval_harness.py` (12 tests covering
fixture loading and scorer logic in isolation, no LLM calls required).

### 2. LLM-as-Judge Confidence Auditor  `[ ]`
**What:** A meta-agent that samples Synthesizer resolutions and independently
grades whether the assigned confidence matches actual synthesis quality —
surfacing over/under-confident outputs. Feeds a calibration curve into the Stats
tab.
**Why flagship:** Shows understanding of the hard problem in agentic systems —
models are bad at self-assessing confidence. Directly extends the existing
`resolution_confidence` gate.
**Hooks into:** `should_loop_back` in `backend/graphs/ingestion_graph.py`
already keys on `confidence < 0.6` — this audits whether that threshold is
meaningful.

### 3. Multi-Model Router with Cost/Quality Tiering  `[x]`
**What:** Route each agent to a model tier by task difficulty — cheap/fast model
for Librarian extraction, stronger model for Critic/Synthesizer reasoning — with
per-request cost tracking and automatic fallback on rate-limit/error.
**Why flagship:** Exactly the LLM-cost-engineering conversation that separates
people who've run agents in production from those who haven't. LinkedIn angle:
"cut inference cost ~60% by matching model tier to agent task."
**Hooks into:** `backend/llm_client.py` `get_llm_client()` — a clean seam with
the existing demo-fallback pattern.

**Status (2026-07-06):** Done. The routing/compression/cost-tracking engine
(`backend/optimization/`) already existed but was dormant — `OptimizedLLMClient.invoke(agent, payload, ...)`
had a signature incompatible with how every agent actually calls the LLM client
(`with_retry(llm_client.invoke, prompt_string, agent="critic")`), so flipping
`ENABLE_TOKEN_OPTIMIZER=true` would have raised `TypeError` on the first real
call. Fixed by giving `OptimizedLLMClient.invoke(prompt, agent="unknown")` the
same call shape as `DemoLLMClient`/`LLMClient`, moving the old payload-based
path to an explicit `invoke_structured()` for non-agent callers (e.g. eval
tooling), and removing the dead `invoke_raw` escape hatch it replaces.
Also unpinned `librarian`/`philosopher`/`critic` in `EchoGraphConfig` (they were
hardcoded to `gpt-4o-mini`, bypassing `ModelRouter` entirely) so complexity-based
routing actually runs for them; `synthesizer`/`scholar` stay pinned to `gpt-4o`
since their output becomes persisted graph truth. Session cost/savings now
surface via `GET /graph/stats` → `optimization` field. `ENABLE_TOKEN_OPTIMIZER`
now defaults to `true` (falls back to `DemoLLMClient` automatically in demo
mode / no API key). New regression coverage:
`tests/unit/test_optimization_engine.py` (9 tests exercising the exact
`with_retry`-driven call path agents use, which had zero prior coverage).

### 4. Provenance & Reasoning-Trace Ledger  `[x]`
**What:** Every synthesized node records its full derivation chain — which raw
nodes, which contradiction, which Synthesizer reasoning, at which loop iteration
— queryable and visualized as a "why does the graph believe this?" trace in the
node inspector.
**Why flagship:** Explainability/provenance is the enterprise-trust story for
agentic knowledge systems. The graph already has `node_type: synthesized` and
`contradiction_resolved` — this makes the reasoning auditable.
**Hooks into:** the `store_node` synthesis persistence in
`backend/graphs/ingestion_graph.py`, node inspector in frontend.

**Status (2026-07-06):** Done. `synthesizer_node` (`backend/agents/synthesizer.py`)
now carries `contradiction_reason`, `credibility_assessment`, and
`loop_iteration` into each resolution alongside the existing reasoning/
confidence. `store_node` (`backend/graphs/ingestion_graph.py`) builds a
`derivation` object per synthesized node — source node IDs (resolving temp IDs
to their real stored IDs), the contradiction Critic flagged, Synthesizer's
reasoning, and the resolution loop iteration — and persists it as a JSON string
in ChromaDB metadata (`KnowledgeStore._prepare_metadata`/`_normalize_node` in
`backend/knowledge_store.py`, since Chroma metadata values must be scalar).

New `GET /graph/nodes/{id}/provenance` endpoint (`backend/main.py`) walks the
derivation chain recursively — a synthesized node can itself derive from an
earlier synthesized node across multiple resolution loops — with cycle/depth
guards, returning a tree of `{id, concept, node_type, derivation, sources[]}`.
Raw/bridge nodes return a single-node tree with `derivation: null`.

Frontend: the node inspector (`frontend/js/app.js` `showInspector`) shows a
"🔍 Why does the graph believe this?" button for `synthesized` nodes only,
lazily fetching and rendering the trace as an indented tree
(`renderProvenanceTrace`), with contradiction reason / credibility assessment /
synthesis reasoning / loop iteration shown per node in the chain.

New test coverage: 4 tests in `tests/unit/test_knowledge_store.py` (derivation
serialization round-trip, `None` default, survives `get_all_nodes`) and 3 in
`tests/integration/test_api_endpoints.py` (endpoint returns a full chain,
handles raw nodes with no derivation, 404s on unknown node ID).

---

## Tier 2 — Production-Systems Breadth (SWE / backend signal)

### 5. Streaming Token-Level Agent Output over WebSocket  `[x]`
**What:** Stream each agent's LLM tokens live to the UI as they generate (not
just start/end events), with backpressure handling. Turns the event stream into
a real-time reasoning theater.
**Why flagship:** Real streaming infra (not polling) is a strong backend signal
and makes the demo visibly impressive. Builds on the existing
batching/compaction/replay event protocol.
**Hooks into:** `backend/events.py`, `emit_event`, existing WebSocket layer.

**Status (2026-07-06):** Done, scoped to Scholar's query answer only —
ingestion agents (Librarian/Philosopher/Critic/Synthesizer) can run many LLM
calls per document (Philosopher alone is O(concepts × existing nodes)), so
streaming all of them would flood the pipeline drawer with noise nobody
watches live. Scholar's answer is the one moment a user is actually staring at
the screen waiting for text, like ChatGPT's typing effect.

All three LLM clients (`DemoLLMClient`, `LLMClient`, `OptimizedLLMClient`) now
share a uniform `invoke_streaming(prompt, agent, on_token)` contract:
- `LLMClient` uses `ChatOpenAI.stream()`.
- `OptimizedLLMClient` uses the raw OpenAI SDK's `stream=True` +
  `stream_options={"include_usage": True}` (needed to get token counts back,
  since streaming responses don't include `.usage` on every chunk) — routing,
  caching, and cost-metric recording all still apply, identical to the
  non-streaming path, just with incremental delivery.
- `DemoLLMClient` has no real model to stream from, so it chunks its scripted
  response word-by-word to preserve the same callback contract for callers.

`scholar_node` (`backend/agents/scholar.py`) calls `invoke_streaming` instead
of `invoke`, emitting a new `agent_token` event per chunk via the existing
`emit_event`/`event_callback` pipeline — no changes needed to
`ConnectionManager`'s batching/compaction/replay logic, since it already
accepts arbitrary event dicts.

Frontend (`frontend/js/app.js`): `agent_token` events accumulate into
`appState.streamingAnswer` and re-render the answer box live via the existing
`renderAnswerTemplate` (so markdown/citation formatting stays consistent
during streaming, not just at the end). The HTTP response's own
`agent_events` replay list explicitly skips `agent_token` entries, since
`result.answer` is already the final complete text by the time that fires —
replaying them would flash a partial reconstruction after the real answer.

**Known limitation:** if a transient failure triggers a retry mid-stream (via
`with_retry`), the UI will see tokens from the failed attempt followed by a
fresh full stream from the retry, rather than a clean single stream. This is
inherent to combining retry-on-failure with token streaming and wasn't
considered worth solving now — it only surfaces on genuine mid-stream network
failure, which is rare.

New test coverage: `tests/unit/test_streaming.py` (6 tests: demo streaming
matches non-streaming output, `OptimizedLLMClient` delivers each chunk via
callback, records correct metrics, actually sets `stream=True` in the request,
and still routes agents by complexity) plus 3 new tests in
`tests/unit/test_agents.py` (scholar emits `agent_token` events whose
concatenation matches the final answer, and a strict test proving scholar
calls `invoke_streaming` and not plain `invoke`).

### 6. Idempotent Ingestion + Content-Hash Deduplication  `[x]`
**What:** Hash incoming content; skip or version re-ingested documents; make the
whole pipeline safe to retry. Return a stable ingestion ID.
**Why flagship:** Idempotency is a classic "this person builds real systems"
marker. Batch ingestion already exists (commit `#C`) — this hardens it.
**Hooks into:** `/ingest/*` endpoints in `backend/main.py`, `store_node`.

**Status (2026-07-06):** Done. Added a module-level `hash_content()` function
(`backend/knowledge_store.py`, SHA-256 over the sanitized document text —
deliberately source-agnostic, so the same text re-submitted under a different
`source_label` or re-fetched from a mirrored URL still counts as a duplicate)
and a second ChromaDB collection, `ingestion_hashes`, used purely as a
content-hash → prior-ingestion-result key-value lookup (no embeddings needed,
so it's cheap to keep separate from the vector-search collection). New
`KnowledgeStore` methods: `find_prior_ingestion(hash)`, `record_ingestion(hash,
result)`; `reset()` now also clears this table so a full graph reset doesn't
leave stale dedup entries blocking re-ingestion.

Wired into `POST /ingest/document` (and `POST /ingest/url`, which delegates to
it) and `POST /ingest/batch`: before running the expensive LangGraph pipeline,
each request checks for a prior ingestion with the same content hash. On a
match, it returns immediately with `status: "duplicate"` and the original
ingestion's stats — zero LLM calls, zero new nodes. `IngestResponse`/
`BatchIngestItemResult.status` now document `"duplicate"` as a valid value
alongside `"success"`/`"failed"`/`"skipped"`.

One implementation note: `hash_content` had to be a **module-level function**,
not a method on `KnowledgeStore`, because the test suite monkeypatches
`main.KnowledgeStore` itself to a lambda constructor — a classmethod call on
the patched name would break. Keeping the hash function name-independent of
the class avoids that coupling.

New test coverage: 6 tests in `tests/unit/test_knowledge_store.py` (hash
determinism, round-trip record/find, upsert-on-same-hash, reset clears the
table) and 4 in `tests/integration/test_api_endpoints.py` (duplicate detection
on `/ingest/document`, different content isn't flagged, duplicate within the
same batch, duplicate against an earlier separate ingestion).

### 7. Rate-Limited, Circuit-Breaking LLM Layer  `[ ]`
**What:** Extend the existing `with_retry` into a full resilience layer —
token-bucket rate limiting, a circuit breaker that trips to demo-mode on
sustained OpenAI failure, exposed via `/health`.
**Why flagship:** `/health` already does real dependency checks and returns 503
on degradation — this completes the resilience story into something genuinely
"production-grade."
**Hooks into:** `backend/retry.py` (already exists), `backend/llm_client.py`,
`/health`.

---

## Recommended Build Order (max impact)

Build **#1 → #3 → #4** first. Together they tell one coherent flagship story:
*"a multi-agent knowledge system that is evaluated, cost-optimized, and
explainable"* — hitting agentic depth, production maturity, and trust/safety in
a single narrative. Strong LinkedIn post + three of the strongest AI-engineering
resume bullets. Start with **#1**, since the eval harness also de-risks building
#3 and #4 (you can measure that they didn't break anything).

**Status as of 2026-07-06:** #1, #3, #4, #5, and #6 are done. Remaining: #2
(LLM judge auditor), #7 (rate limit/circuit breaker).
