"""
Librarian Agent - Extracts key concepts from documents.
Responsible for indexing and initial concept extraction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List
from uuid import uuid4

from backend.events import emit_event
from backend.knowledge_store import KnowledgeStore
from backend.llm_client import get_llm_client
from backend.state import EchoState

logger = logging.getLogger(__name__)


def _parse_concepts(response: str) -> List[Dict[str, str]]:
    """Parse concept blocks from model output."""
    concepts: List[Dict[str, str]] = []
    current_title = ""
    current_summary_parts: List[str] = []

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("CONCEPT:"):
            if current_title and current_summary_parts:
                concepts.append(
                    {
                        "concept": current_title[:200],
                        "summary": " ".join(current_summary_parts)[:1000],
                    }
                )
            current_title = line.replace("CONCEPT:", "", 1).strip()
            current_summary_parts = []
            continue

        if line.startswith("SUMMARY:"):
            current_summary_parts.append(line.replace("SUMMARY:", "", 1).strip())
            continue

        # Keep multi-line summary continuation.
        if current_title:
            current_summary_parts.append(line)

    if current_title and current_summary_parts:
        concepts.append(
            {
                "concept": current_title[:200],
                "summary": " ".join(current_summary_parts)[:1000],
            }
        )

    return concepts


def librarian_node(state: EchoState, knowledge_store: KnowledgeStore) -> EchoState:
    """Extract 5-10 key concepts from the source content and search overlap."""
    logger.info("Librarian agent starting concept extraction")
    emit_event(
        state,
        event="agent_start",
        agent="librarian",
        data={"agent": "librarian", "label": "Extracting concepts"},
    )

    try:
        raw_content = (state.get("raw_content") or "").strip()
        if not raw_content:
            raise ValueError("Document content is empty")

        llm_client = get_llm_client()
        extraction_prompt = f"""
You are a knowledge extraction expert. Use step-by-step thinking to extract the most important concepts.

STEP 1 — Understand the document:
Identify the domain, the central argument or purpose, and the intended audience.

STEP 2 — Extract 5-10 key concepts:
Each concept must be a distinct, self-contained knowledge unit that captures a specific claim,
finding, method, or insight from the document. Ask yourself: "Would someone need to know this
concept to understand the document's contribution?"

Avoid vague or overly broad titles. Prefer precise, specific claims.

For each concept return exactly:
CONCEPT: <short title capturing the specific claim, max 200 chars>
SUMMARY: <2-3 sentences that state the core claim clearly with supporting context, max 1000 chars>

Document:
{raw_content[:12000]}

Begin with a brief 1-2 sentence analysis of the document, then list the concepts.
"""

        response = llm_client.invoke(extraction_prompt)
        parsed_concepts = _parse_concepts(response)

        if not parsed_concepts:
            fallback = raw_content.split("\n", 1)[0][:200] or "General Concept"
            parsed_concepts = [
                {
                    "concept": fallback,
                    "summary": raw_content[:900],
                }
            ]

        parsed_concepts = parsed_concepts[:10]

        embeddings = llm_client.embed_texts([item["summary"] for item in parsed_concepts])

        new_concepts: List[Dict] = []
        existing_nodes: List[Dict] = []

        for idx, concept_data in enumerate(parsed_concepts):
            temp_id = f"temp_{uuid4()}"
            concept = {
                "id": temp_id,
                "concept": concept_data["concept"],
                "summary": concept_data["summary"],
                "source": state["source_label"],
                "node_type": "raw",
                "confidence": 1.0,
                "contradiction_resolved": False,
                "connected_to": [],
                "relationship_types": [],
                "embedding": embeddings[idx],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "times_retrieved": 0,
            }
            new_concepts.append(concept)

            similar_nodes = knowledge_store.search_similar(
                concept["embedding"],
                top_k=8,
                threshold=0.85,
            )
            existing_nodes.extend(similar_nodes)

            emit_event(
                state,
                event="concept_extracted",
                agent="librarian",
                data={
                    "concept": concept["concept"],
                    "node_id": temp_id,
                    "overlap_count": len(similar_nodes),
                    "is_new": len(similar_nodes) == 0,
                },
            )

        # Deduplicate existing nodes by ID.
        dedup_existing: Dict[str, Dict] = {}
        for node in existing_nodes:
            dedup_existing[node["id"]] = node

        state["new_concepts"] = new_concepts
        state["existing_nodes"] = list(dedup_existing.values())
        state["current_agent"] = "librarian"

        logger.info(
            "Librarian extracted %s concepts and found %s overlapping nodes",
            len(new_concepts),
            len(dedup_existing),
        )
        return state

    except Exception:
        logger.exception("Error in librarian_node")
        emit_event(
            state,
            event="error",
            agent="librarian",
            data={"message": "Concept extraction failed"},
        )
        raise
