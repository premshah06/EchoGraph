"""
Philosopher Agent - Discovers semantic relationships between concepts.
Responsible for connecting new knowledge to existing knowledge.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from backend.events import emit_event
from backend.llm_client import get_llm_client
from backend.state import EchoState

logger = logging.getLogger(__name__)

ALLOWED_RELATIONSHIPS = {
    "supports",
    "extends",
    "reframes",
    "questions",
    "is_prerequisite_of",
    "bridge",
}


def _parse_relationship(response: str) -> Optional[Dict[str, object]]:
    relationship_type: Optional[str] = None
    strength = 0.5
    explanation = ""

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if line.startswith("RELATIONSHIP:"):
            parsed = line.replace("RELATIONSHIP:", "", 1).strip().lower()
            if parsed in ALLOWED_RELATIONSHIPS:
                relationship_type = parsed
        elif line.startswith("STRENGTH:"):
            try:
                strength = float(line.replace("STRENGTH:", "", 1).strip())
            except ValueError:
                strength = 0.5
        elif line.startswith("EXPLANATION:"):
            explanation = line.replace("EXPLANATION:", "", 1).strip()

    if not relationship_type:
        return None

    strength = max(0.0, min(1.0, strength))
    if not explanation:
        explanation = "Semantic relationship detected"

    return {
        "relationship_type": relationship_type,
        "strength": strength,
        "explanation": explanation,
    }


def _relationship_prompt(concept_a: Dict, concept_b: Dict) -> str:
    return f"""
You are a knowledge graph expert. Reason step by step before classifying the relationship.

CONCEPT A:
Title: {concept_a['concept']}
Summary: {concept_a['summary']}
Source: {concept_a.get('source', 'unknown')}

CONCEPT B:
Title: {concept_b['concept']}
Summary: {concept_b['summary']}
Source: {concept_b.get('source', 'unknown')}

Think through each step before responding:
1. Core claim of Concept A: What specific assertion does it make?
2. Core claim of Concept B: What specific assertion does it make?
3. Relationship analysis: Does B reinforce A (supports), build upon A (extends), challenge A's
   framing (reframes), cast doubt on A (questions), need to be understood before A
   (is_prerequisite_of), or connect two otherwise unrelated domains (bridge)?
4. Strength: How strong and direct is this relationship? (0.0 = none, 1.0 = very strong)

Only report a relationship if it is genuinely meaningful — not merely topically similar.

Respond exactly:
RELATIONSHIP: <type or none>
STRENGTH: <0.0-1.0>
EXPLANATION: <one sentence grounded in the reasoning above>
"""


def philosopher_node(state: EchoState) -> EchoState:
    """Find semantic connections between new concepts and known nodes."""
    logger.info("Philosopher agent starting relationship detection")
    emit_event(
        state,
        event="agent_start",
        agent="philosopher",
        data={"agent": "philosopher", "label": "Finding relationships"},
    )

    try:
        llm_client = get_llm_client()
        connections: List[Dict] = []

        new_concepts = state.get("new_concepts", [])
        existing_nodes = state.get("existing_nodes", [])

        # Connect each new concept to likely overlapping existing nodes.
        for new_concept in new_concepts:
            for existing_node in existing_nodes:
                response = llm_client.invoke(_relationship_prompt(new_concept, existing_node))
                parsed = _parse_relationship(response)
                if not parsed or parsed["strength"] < 0.3:
                    continue

                connection = {
                    "node_a_id": new_concept["id"],
                    "node_b_id": existing_node["id"],
                    **parsed,
                }
                connections.append(connection)

                emit_event(
                    state,
                    event="connection_found",
                    agent="philosopher",
                    data={
                        "from": connection["node_a_id"],
                        "to": connection["node_b_id"],
                        "type": connection["relationship_type"],
                        "strength": connection["strength"],
                    },
                )

        # Detect relationships among newly extracted concepts as well.
        for i in range(len(new_concepts)):
            concept_a = new_concepts[i]
            for concept_b in new_concepts[i + 1 :]:
                response = llm_client.invoke(_relationship_prompt(concept_a, concept_b))
                parsed = _parse_relationship(response)
                if not parsed or parsed["strength"] < 0.3:
                    continue

                connection = {
                    "node_a_id": concept_a["id"],
                    "node_b_id": concept_b["id"],
                    **parsed,
                }
                connections.append(connection)

                emit_event(
                    state,
                    event="connection_found",
                    agent="philosopher",
                    data={
                        "from": connection["node_a_id"],
                        "to": connection["node_b_id"],
                        "type": connection["relationship_type"],
                        "strength": connection["strength"],
                    },
                )

        state["connections"] = connections
        state["current_agent"] = "philosopher"

        logger.info("Philosopher found %s relationships", len(connections))
        return state

    except Exception:
        logger.exception("Error in philosopher_node")
        emit_event(
            state,
            event="error",
            agent="philosopher",
            data={"message": "Relationship detection failed"},
        )
        raise
