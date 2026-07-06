# Portfolio Notes

Ideas and engineering decisions worth mining for a resume bullet, LinkedIn post,
or interview talking point. Append a new dated entry each time something like
this comes up — don't rewrite history, just add on.

---

## 2026-07-06 — Self-updating architecture diagrams via git hooks (token-conscious tooling design)

**What I built:** An automated pipeline that regenerates a visual architecture
diagram (grouped nodes, arrows, hand-drawn SVG style) whenever the system's
design changes — without a human remembering to update it, and without the
generation cost of a live diagramming tool.

**The interesting decision (this is the resume-worthy part):** I was offered a
capability that could do this via an MCP-connected diagramming tool (Excalidraw),
which round-trips full scene JSON through an LLM conversation on every change —
expensive and slow to scale. Instead, I designed a cheaper architecture:

- A YAML file (`docs/architecture.yaml`) as the single source of truth for
  components/connections — human-editable, diffable, git-trackable.
- A skill that renders that YAML into a self-contained HTML/SVG diagram file
  directly (no external tool calls), embedding its own source data as a comment
  so future runs can cheaply detect "did anything actually change" before
  re-rendering.
- Three git hooks (`post-commit`, `post-merge`, `post-checkout`) that gate the
  expensive step (an LLM invocation) behind a free shell-level check
  (`git diff --name-only ... | grep`) — so the automation costs *nothing* on
  the 95% of commits/pulls/checkouts that don't touch architecture.

**Why this is worth talking about:** It's a concrete example of designing for
cost/efficiency at the tooling-architecture level, not just the code level —
recognizing that "just call the fancy tool every time" doesn't scale, and
building a cheap gate in front of an expensive operation instead. Also
demonstrates thinking through edge cases in automation triggers (merges,
rebases, branch switches, pulls) rather than the naive single-hook version.

**Possible framings:**
- Resume bullet: *"Designed a git-hook-driven documentation pipeline that
  auto-regenerates architecture diagrams on relevant changes only, cutting
  automation cost by gating expensive LLM calls behind free diff checks."*
- LinkedIn angle: a short post about "the cheap trick that made an AI-powered
  dev tool actually affordable to run on every commit" — contrasting the naive
  MCP round-trip approach vs. the gated static-render approach.
- Interview talking point: an example of thinking about cost/latency tradeoffs
  when wiring LLMs into developer workflows, not just "can it work" but "should
  it run this often."

**Artifacts from this work:**
- `docs/architecture.yaml`
- `.claude/skills/update-architecture-diagram/SKILL.md`
- `.git/hooks/post-commit`, `.git/hooks/post-merge`, `.git/hooks/post-checkout`

---

## 2026-07-06 — Shipped three flagship agentic-engineering features on EchoGraph

**What I built:** Three features chosen specifically to demonstrate senior-level
agentic engineering depth: an agent evaluation harness, a working multi-model
cost router, and a provenance/reasoning-trace ledger. Full design list in
`docs/features.md`.

**1. Fixed a dormant cost-optimization engine instead of building a new one.**
Investigation before building found `backend/optimization/` — a fully-designed
model-routing/cost-tracking engine (payload compression, complexity-based model
selection, prompt caching, per-call cost accounting) — already existed but was
never actually reachable: `OptimizedLLMClient.invoke(agent, payload, ...)` had a
signature that didn't match how any agent actually calls the LLM client
(`with_retry(llm_client.invoke, prompt_string, agent="critic")`). Flipping the
feature flag on would have crashed on the very first real call. Also found every
agent was hardcoded to a specific model in the config, silently bypassing the
router's complexity-based selection entirely — so "smart routing" was dead code
even where the interface worked. Fixed both: matched the client's call shape to
what agents actually send, and unpinned the agents whose complexity should
genuinely drive model choice. This is a stronger story than building fresh: it
shows the instinct to verify existing infrastructure actually works end-to-end
before trusting it, not just that it compiles.

**2. Built an eval harness that found a real bug within minutes of its first run.**
The harness runs golden documents through the real ingestion/query graphs
(no mocks, same code path as the API) and scores concept-recall, contradiction
detection, and confidence calibration against labeled expectations. First run
in demo mode surfaced a genuine pre-existing bug: the demo LLM's concept-
extraction fallback was parsing the *prompt's own instructional text* instead of
returning a well-formed fake response, once the prompt became realistic
multi-line text. Flagged, not silently fixed (outside that session's scope) —
but it's a clean, honest anecdote for "why evals matter": the harness caught a
real defect immediately, which is the whole argument for building one.

**3. Provenance ledger — reasoning traceability for synthesized knowledge.**
Every node the Synthesizer agent produces now carries a `derivation` record
(source node IDs, the contradiction Critic flagged, the synthesis reasoning,
which resolution-loop iteration produced it), queryable via
`GET /graph/nodes/{id}/provenance` and visualized as a recursive "why does the
graph believe this?" trace in the UI — since a synthesized node can itself be
built from an earlier synthesized node across multiple contradiction-resolution
loops.

**Why this is worth talking about:** All three together tell one coherent
narrative — a multi-agent system that is *evaluated* (correctness is checked,
not assumed), *cost-optimized* (model choice matches task complexity, and the
routing is verified to actually run), and *explainable* (every synthesized
claim can be traced back to why the system believes it). That combination is
the senior-engineer signal: not just "I built agents that work," but "I built
the infrastructure that proves they work, keeps them affordable, and makes
their conclusions auditable."

**Possible framings:**
- Resume bullet: *"Built an agent evaluation harness that runs golden test
  cases through the live LangGraph pipeline, catching a reasoning-fallback
  defect within its first run; fixed a dormant multi-model cost-routing engine
  and shipped a full provenance ledger for synthesized knowledge claims."*
- LinkedIn angle: "I found working infrastructure that was actually dead code —
  here's how I found it and what fixing it taught me about verifying agentic
  systems end-to-end, not just that the pieces exist."
- Interview talking point: the instinct to check whether "already built"
  features are load-bearing before writing anything new — and treating an eval
  harness's first bug catch as validation of the approach, not a distraction
  from the roadmap.

**Artifacts from this work:**
- `backend/optimization/engine.py`, `backend/optimization/configs/echograph.py` (router fix)
- `backend/eval/` — `fixtures.py`, `runner.py`, `scorer.py`, `run.py`, `fixtures/*.json` (eval harness)
- `backend/agents/synthesizer.py`, `backend/graphs/ingestion_graph.py`, `backend/knowledge_store.py`, `GET /graph/nodes/{id}/provenance` (provenance ledger)
- `tests/unit/test_optimization_engine.py`, `tests/unit/test_eval_harness.py`, plus additions to `tests/unit/test_knowledge_store.py` and `tests/integration/test_api_endpoints.py`
- Full feature backlog and status: `docs/features.md`
