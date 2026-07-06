"""
Synthesizer Agent - Resolves contradictions through synthesis.
Responsible for merging conflicting knowledge into nuanced understanding.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from backend.events import emit_event
from backend.knowledge_store import KnowledgeStore
from backend.llm_client import get_llm_client
from backend.retry import with_retry
from backend.state import EchoState

logger = logging.getLogger(__name__)


def _parse_synthesis_response(response: str) -> Dict[str, object]:
    synthesis_text = ""
    confidence = 0.5
    reasoning = ""

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if line.startswith("SYNTHESIS:"):
            synthesis_text = line.replace("SYNTHESIS:", "", 1).strip()[:2000]
        elif line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.replace("CONFIDENCE:", "", 1).strip())
            except ValueError:
                confidence = 0.5
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "", 1).strip()

    confidence = max(0.0, min(1.0, confidence))
    if not synthesis_text:
        synthesis_text = "Available evidence supports a conditional synthesis across sources."
    if not reasoning:
        reasoning = "The synthesis balances source credibility and claim consistency."

    return {
        "synthesis_text": synthesis_text,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def synthesizer_node(state: EchoState, knowledge_store: KnowledgeStore) -> EchoState:
    """Resolve detected contradictions and assign confidence."""
    loop_count = state.get("loop_count", 0)
    logger.info("Synthesizer agent running (loop %s)", loop_count)

    emit_event(
        state,
        event="agent_start",
        agent="synthesizer",
        data={"agent": "synthesizer", "label": "Resolving contradictions"},
    )
    emit_event(
        state,
        event="resolution_start",
        agent="synthesizer",
        data={"loop": loop_count},
    )

    try:
        llm_client = get_llm_client()
        resolutions: List[Dict] = []

        for contradiction in state.get("contradictions", []):
            old_node = knowledge_store.get_node(contradiction["old_node_id"])
            if not old_node:
                continue

            synthesis_prompt = f"""
You are a knowledge synthesis expert. Use dialectical reasoning to resolve this contradiction
into a higher-order understanding that captures the truth in both positions.

THESIS (Existing Claim):
Title: {old_node['concept']}
Summary: {old_node['summary']}
Source: {old_node['source']}

ANTITHESIS (New Claim):
Title: {contradiction['new_concept']}
Summary: {contradiction.get('new_concept_summary', '')}
Source: {contradiction['new_source']}

CONFLICT: {contradiction['reason']}
CREDIBILITY ASSESSMENT: {contradiction['credibility_assessment']}

Reason dialectically through these steps:
1. Strongest version of the thesis: What is the best case for the existing claim being true?
2. Strongest version of the antithesis: What is the best case for the new claim being true?
3. Conditional truth: Under what specific conditions is each claim valid?
4. Synthesis: What higher-order statement reconciles both claims — acknowledging where each
   holds, where each fails, and what the combined evidence actually supports?
5. Confidence: How certain are you? Consider source quality, claim specificity, and
   whether the synthesis resolves the conflict or merely defers it.

Respond exactly:
SYNTHESIS: <2-3 sentences capturing the reconciled position with appropriate nuance, max 2000 chars>
CONFIDENCE: <0.0-1.0 — be honest; low confidence is valid when evidence is genuinely ambiguous>
REASONING: <what makes this synthesis valid despite the conflict>
"""

            parsed = _parse_synthesis_response(with_retry(llm_client.invoke, synthesis_prompt, agent="synthesizer"))
            resolution = {
                "contradiction_id": f"{contradiction['old_node_id']}_{contradiction['new_concept_id']}",
                "synthesis_text": parsed["synthesis_text"],
                "confidence": parsed["confidence"],
                "sources_considered": [old_node["source"], contradiction["new_source"]],
                "reasoning": parsed["reasoning"],
                "old_node_id": contradiction["old_node_id"],
                "new_concept": contradiction["new_concept"],
                "new_concept_id": contradiction["new_concept_id"],
                "contradiction_reason": contradiction["reason"],
                "credibility_assessment": contradiction["credibility_assessment"],
                "loop_iteration": loop_count,
            }
            resolutions.append(resolution)

        avg_confidence = (
            sum(item["confidence"] for item in resolutions) / len(resolutions)
            if resolutions
            else 1.0
        )

        state["resolutions"] = resolutions
        state["resolution_confidence"] = avg_confidence
        state["current_agent"] = "synthesizer"

        emit_event(
            state,
            event="resolution_done",
            agent="synthesizer",
            data={
                "confidence": avg_confidence,
                "synthesis": resolutions[0]["synthesis_text"] if resolutions else "",
            },
        )

        if avg_confidence < 0.6 and loop_count < 3:
            emit_event(
                state,
                event="loop_back",
                agent="synthesizer",
                data={"reason": "Low confidence synthesis", "loop_count": loop_count},
            )

        return state

    except Exception:
        logger.exception("Error in synthesizer_node")
        emit_event(
            state,
            event="error",
            agent="synthesizer",
            data={"message": "Synthesis failed"},
        )
        raise
