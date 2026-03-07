"""
Critic Agent - Detects contradictions and conflicts.
Responsible for challenging new knowledge against existing knowledge.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from backend.events import emit_event
from backend.knowledge_store import KnowledgeStore
from backend.llm_client import get_llm_client
from backend.state import EchoState

logger = logging.getLogger(__name__)


def _parse_contradiction_response(response: str) -> Dict[str, str]:
    data = {
        "contradiction": "no",
        "reason": "",
        "credibility": "",
    }

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if line.startswith("CONTRADICTION:"):
            data["contradiction"] = line.replace("CONTRADICTION:", "", 1).strip().lower()
        elif line.startswith("REASON:"):
            data["reason"] = line.replace("REASON:", "", 1).strip()
        elif line.startswith("CREDIBILITY:"):
            data["credibility"] = line.replace("CREDIBILITY:", "", 1).strip()

    return data


def critic_node(state: EchoState, knowledge_store: KnowledgeStore) -> EchoState:
    """Analyze new concepts for contradictions with similar existing nodes."""
    logger.info("Critic agent starting contradiction detection")
    emit_event(
        state,
        event="agent_start",
        agent="critic",
        data={"agent": "critic", "label": "Checking contradictions"},
    )

    try:
        llm_client = get_llm_client()
        contradictions: List[Dict] = []
        seen_pairs = set()

        for new_concept in state.get("new_concepts", []):
            similar_nodes = knowledge_store.search_similar(
                new_concept["embedding"],
                top_k=10,
                threshold=0.75,
            )

            for existing_node in similar_nodes:
                pair_key = (new_concept["id"], existing_node["id"])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                contradiction_prompt = f"""
You are a critical thinking expert. Use step-by-step logical analysis to determine whether
these two claims genuinely contradict each other — not merely differ in emphasis or scope.

EXISTING CLAIM:
Title: {existing_node['concept']}
Summary: {existing_node['summary']}
Source: {existing_node['source']}

NEW CLAIM:
Title: {new_concept['concept']}
Summary: {new_concept['summary']}
Source: {new_concept['source']}

Analyze step by step:
1. Core assertion of the existing claim: What specific factual or causal claim does it make?
2. Core assertion of the new claim: What specific factual or causal claim does it make?
3. Scope check: Do both claims address the same subject, domain, and conditions?
   If they address different contexts or populations, they may not truly contradict.
4. Logical compatibility test: Can both claims be simultaneously true?
   If yes under any reasonable interpretation → no contradiction.
5. Severity: If incompatible, is this a direct logical conflict (genuine contradiction)
   or a difference in nuance/emphasis (not a contradiction)?

Only respond CONTRADICTION: yes if the claims make mutually exclusive assertions
about the same topic under the same conditions.

Respond exactly:
CONTRADICTION: <yes or no>
REASON: <the specific logical incompatibility, or N/A>
CREDIBILITY: <which source is more credible and why, or N/A>
"""

                parsed = _parse_contradiction_response(llm_client.invoke(contradiction_prompt))
                is_contradiction = parsed["contradiction"] == "yes"

                if not is_contradiction:
                    continue

                reason = parsed["reason"] or "Claims conflict semantically"
                credibility = parsed["credibility"] or "Credibility could not be determined"
                contradiction = {
                    "old_node_id": existing_node["id"],
                    "new_concept_id": new_concept["id"],
                    "new_concept": new_concept["concept"],
                    "new_concept_summary": new_concept["summary"],
                    "reason": reason,
                    "old_source": existing_node["source"],
                    "new_source": new_concept["source"],
                    "credibility_assessment": credibility,
                }
                contradictions.append(contradiction)

                emit_event(
                    state,
                    event="contradiction_found",
                    agent="critic",
                    data={
                        "node_a": existing_node["id"],
                        "node_b": new_concept["id"],
                        "reason": reason,
                    },
                )

        state["contradictions"] = contradictions
        state["contradiction_found"] = len(contradictions) > 0
        state["current_agent"] = "critic"

        logger.info("Critic detected %s contradictions", len(contradictions))
        return state

    except Exception:
        logger.exception("Error in critic_node")
        emit_event(
            state,
            event="error",
            agent="critic",
            data={"message": "Contradiction analysis failed"},
        )
        raise
