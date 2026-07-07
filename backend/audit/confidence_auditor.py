"""
LLM-as-Judge Confidence Auditor.

Synthesizer assigns its own confidence score to every contradiction
resolution it produces (0.0-1.0), which also gates whether the ingestion
pipeline loops back for another resolution attempt (see
backend/graphs/ingestion_graph.py should_loop_back). But a model grading its
own output is a well-known blind spot — nothing today checks whether that
self-reported confidence is actually trustworthy.

This module re-reads already-synthesized nodes from the knowledge store,
reconstructs the original contradiction from each node's `derivation` record
(added for the provenance ledger — source node IDs, the contradiction reason,
credibility assessment, and Synthesizer's own reasoning), and asks a fresh LLM
call to independently judge: given this same evidence, what confidence would
you assign? The gap between the judge's score and Synthesizer's original
score is the calibration signal.

Deliberately NOT wired into the ingestion pipeline — auditing costs an extra
LLM call per resolution, and most resolutions are never scrutinized by a
human. This runs on-demand via POST /audit/confidence (see backend/main.py),
sampling from what's already persisted, the same way the eval harness runs
separately from the hot path rather than inline with every ingest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.knowledge_store import KnowledgeStore
from backend.llm_client import get_llm_client
from backend.retry import default_circuit_breaker, default_rate_limiter, with_retry

logger = logging.getLogger(__name__)

# A gap at or above this threshold is flagged as a miscalibration.
MISCALIBRATION_THRESHOLD = 0.25


@dataclass
class AuditResult:
    node_id: str
    concept: str
    synthesizer_confidence: float
    judge_confidence: float
    gap: float
    is_miscalibrated: bool
    judge_reasoning: str
    direction: str  # "overconfident" | "underconfident" | "well_calibrated"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "concept": self.concept,
            "synthesizer_confidence": round(self.synthesizer_confidence, 3),
            "judge_confidence": round(self.judge_confidence, 3),
            "gap": round(self.gap, 3),
            "is_miscalibrated": self.is_miscalibrated,
            "judge_reasoning": self.judge_reasoning,
            "direction": self.direction,
        }


def _parse_judge_response(response: str) -> Dict[str, Any]:
    confidence = 0.5
    reasoning = ""

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.replace("CONFIDENCE:", "", 1).strip())
            except ValueError:
                confidence = 0.5
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "", 1).strip()

    confidence = max(0.0, min(1.0, confidence))
    if not reasoning:
        reasoning = "Judge did not provide reasoning."

    return {"confidence": confidence, "reasoning": reasoning}


def _judge_prompt(node: Dict[str, Any], derivation: Dict[str, Any], source_node: Optional[Dict[str, Any]]) -> str:
    thesis_summary = source_node["summary"] if source_node else "(original claim not available)"
    thesis_source = source_node["source"] if source_node else "unknown"

    return f"""
You are an independent auditor reviewing another AI system's confidence calibration.
You are NOT told what confidence score it originally assigned — judge the evidence fresh.

THESIS (Existing Claim):
{thesis_summary}
Source: {thesis_source}

ANTITHESIS (New Claim) and the synthesized resolution:
{node['summary']}

CONFLICT THAT WAS RESOLVED: {derivation.get('contradiction_reason', 'unknown')}
CREDIBILITY ASSESSMENT USED: {derivation.get('credibility_assessment', 'unknown')}
STATED REASONING FOR THE SYNTHESIS: {derivation.get('synthesis_reasoning', 'unknown')}

Independently assess: given only this evidence, how confident should one actually be
in this synthesized resolution? Consider source quality, whether the synthesis
genuinely reconciles the conflict or merely defers it, and whether the reasoning
given is substantive or generic.

Respond exactly:
CONFIDENCE: <0.0-1.0 — your own independent judgment>
REASONING: <one sentence on what drove your score>
"""


def _find_source_node(node: Dict[str, Any], derivation: Dict[str, Any], knowledge_store: KnowledgeStore) -> Optional[Dict[str, Any]]:
    """
    source_node_ids includes both the pre-existing node and the newly
    synthesized concept; we only need one representative thesis to judge
    against — the first ID that isn't this node itself.
    """
    for source_id in derivation.get("source_node_ids", []):
        if source_id == node["id"]:
            continue
        try:
            candidate = knowledge_store.get_node(source_id)
        except Exception:
            logger.exception("Error looking up source node %s during audit", source_id)
            continue
        if candidate:
            return candidate
    return None


def audit_node(node: Dict[str, Any], knowledge_store: KnowledgeStore) -> Optional[AuditResult]:
    """Audit a single synthesized node. Returns None if it has no derivation to audit."""
    derivation = node.get("derivation")
    if not derivation:
        return None

    llm_client = get_llm_client()
    source_node = _find_source_node(node, derivation, knowledge_store)

    prompt = _judge_prompt(node, derivation, source_node)
    parsed = _parse_judge_response(with_retry(
        llm_client.invoke, prompt, agent="confidence_auditor",
        rate_limiter=default_rate_limiter, circuit_breaker=default_circuit_breaker,
    ))

    synthesizer_confidence = float(node.get("confidence", 0.5))
    judge_confidence = parsed["confidence"]
    gap = judge_confidence - synthesizer_confidence

    if judge_confidence > synthesizer_confidence + 0.05:
        direction = "underconfident"
    elif judge_confidence < synthesizer_confidence - 0.05:
        direction = "overconfident"
    else:
        direction = "well_calibrated"

    return AuditResult(
        node_id=node["id"],
        concept=node["concept"],
        synthesizer_confidence=synthesizer_confidence,
        judge_confidence=judge_confidence,
        gap=abs(gap),
        is_miscalibrated=abs(gap) >= MISCALIBRATION_THRESHOLD,
        judge_reasoning=parsed["reasoning"],
        direction=direction,
    )


def run_audit(knowledge_store: KnowledgeStore, sample_size: Optional[int] = None) -> Dict[str, Any]:
    """
    Audit synthesized nodes in the knowledge store, up to sample_size (None = all).

    Returns a summary dict with per-node results and aggregate calibration
    stats (mean gap, miscalibration rate, most-overconfident node), suitable
    for a scorecard or a Stats-tab calibration view.
    """
    all_nodes = knowledge_store.get_all_nodes()
    synthesized = [n for n in all_nodes if n.get("node_type") == "synthesized" and n.get("derivation")]

    if sample_size is not None:
        synthesized = synthesized[:sample_size]

    results: List[AuditResult] = []
    for node in synthesized:
        result = audit_node(node, knowledge_store)
        if result:
            results.append(result)

    if not results:
        return {
            "audited_count": 0,
            "mean_gap": 0.0,
            "miscalibration_rate": 0.0,
            "results": [],
        }

    mean_gap = sum(r.gap for r in results) / len(results)
    miscalibrated = [r for r in results if r.is_miscalibrated]
    most_overconfident = max(
        (r for r in results if r.direction == "overconfident"),
        key=lambda r: r.gap,
        default=None,
    )

    return {
        "audited_count": len(results),
        "mean_gap": round(mean_gap, 3),
        "miscalibration_rate": round(len(miscalibrated) / len(results), 3),
        "most_overconfident_node_id": most_overconfident.node_id if most_overconfident else None,
        "results": [r.to_dict() for r in results],
    }
