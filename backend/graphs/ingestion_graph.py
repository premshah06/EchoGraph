"""
Ingestion Graph - LangGraph orchestration for document processing.
Flow: Librarian -> Philosopher -> Critic -> (Synthesizer?) -> Store.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Literal

from langgraph.graph import END, StateGraph

from backend.agents.critic import critic_node
from backend.agents.librarian import librarian_node
from backend.agents.philosopher import philosopher_node
from backend.agents.synthesizer import synthesizer_node
from backend.events import emit_event
from backend.knowledge_store import KnowledgeStore
from backend.llm_client import get_llm_client
from backend.state import EchoState

logger = logging.getLogger(__name__)


def should_synthesize(state: EchoState) -> Literal["synthesizer", "store"]:
    """Route contradictions to synthesis, otherwise persist directly."""
    return "synthesizer" if state.get("contradiction_found", False) else "store"


def should_loop_back(state: EchoState) -> Literal["critic", "store"]:
    """Loop back when synthesis confidence is low and retry budget remains."""
    confidence = float(state.get("resolution_confidence", 1.0))
    loop_count = int(state.get("loop_count", 0))
    if confidence < 0.6 and loop_count < 3:
        return "critic"
    return "store"


def create_store_node(knowledge_store: KnowledgeStore):
    """Create a persistence node closure bound to the shared KnowledgeStore."""

    def store_node(state: EchoState) -> EchoState:
        emit_event(
            state,
            event="agent_start",
            agent="store",
            data={"agent": "store", "label": "Persisting nodes"},
        )

        logger.info("Store node persisting graph updates")
        temp_to_stored: Dict[str, str] = {}
        stored_ids = []

        try:
            # Persist raw concepts.
            for concept in state.get("new_concepts", []):
                node_payload = dict(concept)
                temp_id = str(node_payload.get("id", ""))
                if temp_id.startswith("temp_"):
                    node_payload.pop("id", None)

                stored_id = knowledge_store.add_node(node_payload)
                stored_ids.append(stored_id)
                concept["id"] = stored_id
                if temp_id:
                    temp_to_stored[temp_id] = stored_id

                emit_event(
                    state,
                    event="node_stored",
                    agent="store",
                    data={"node_id": stored_id, "type": concept.get("node_type", "raw")},
                )

            # Persist synthesized resolutions.
            llm_client = get_llm_client()
            for resolution in state.get("resolutions", []):
                summary = resolution.get("synthesis_text", "")[:2000]
                new_concept_id = resolution.get("new_concept_id", "")
                resolved_new_concept_id = temp_to_stored.get(new_concept_id, new_concept_id)

                derivation = {
                    "source_node_ids": [
                        nid for nid in (resolution.get("old_node_id"), resolved_new_concept_id) if nid
                    ],
                    "contradiction_reason": resolution.get("contradiction_reason", ""),
                    "credibility_assessment": resolution.get("credibility_assessment", ""),
                    "synthesis_reasoning": resolution.get("reasoning", ""),
                    "loop_iteration": resolution.get("loop_iteration", 0),
                }

                synth_node = {
                    "concept": f"Synthesis: {resolution.get('new_concept', 'Resolved Claim')[:160]}",
                    "summary": summary,
                    "source": " | ".join(resolution.get("sources_considered", []))[:1000],
                    "node_type": "synthesized",
                    "confidence": float(resolution.get("confidence", 0.5)),
                    "contradiction_resolved": True,
                    "connected_to": [],
                    "relationship_types": [],
                    "embedding": llm_client.embed_text(summary or "Synthesis"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "times_retrieved": 0,
                    "derivation": derivation,
                }
                stored_id = knowledge_store.add_node(synth_node)
                stored_ids.append(stored_id)

                old_id = resolution.get("old_node_id")
                if old_id:
                    knowledge_store.add_edge(
                        {
                            "node_a_id": stored_id,
                            "node_b_id": old_id,
                            "relationship_type": "synthesizes",
                            "strength": resolution.get("confidence", 0.5),
                            "explanation": resolution.get("reasoning", "Synthesized contradiction resolution"),
                        }
                    )

                emit_event(
                    state,
                    event="node_stored",
                    agent="store",
                    data={"node_id": stored_id, "type": "synthesized"},
                )

            # Persist discovered connections with resolved IDs.
            for connection in state.get("connections", []):
                source_id = temp_to_stored.get(connection["node_a_id"], connection["node_a_id"])
                target_id = temp_to_stored.get(connection["node_b_id"], connection["node_b_id"])

                if source_id == target_id:
                    continue

                knowledge_store.add_edge(
                    {
                        "node_a_id": source_id,
                        "node_b_id": target_id,
                        "relationship_type": connection.get("relationship_type", "related"),
                        "strength": float(connection.get("strength", 0.5)),
                        "explanation": connection.get("explanation", "Semantic relationship"),
                    }
                )

            state["processing_complete"] = True
            state["current_agent"] = "store"

            emit_event(
                state,
                event="ingestion_complete",
                agent="store",
                data={
                    "new_nodes": len(stored_ids),
                    "edges": len(state.get("connections", [])),
                },
            )

            logger.info(
                "Store node finished: %s nodes and %s edges",
                len(stored_ids),
                len(state.get("connections", [])),
            )
            return state
        except Exception:
            logger.exception("Error in store_node")
            emit_event(
                state,
                event="error",
                agent="store",
                data={"message": "Failed to persist ingestion results"},
            )
            raise

    return store_node


def create_ingestion_graph(knowledge_store: KnowledgeStore) -> StateGraph:
    """Create and compile the ingestion graph with conditional routing."""
    logger.info("Creating ingestion graph")
    workflow = StateGraph(EchoState)

    def librarian_wrapper(state: EchoState) -> EchoState:
        return librarian_node(state, knowledge_store)

    def critic_wrapper(state: EchoState) -> EchoState:
        return critic_node(state, knowledge_store)

    def synthesizer_wrapper(state: EchoState) -> EchoState:
        state["loop_count"] = int(state.get("loop_count", 0)) + 1
        return synthesizer_node(state, knowledge_store)

    workflow.add_node("librarian", librarian_wrapper)
    workflow.add_node("philosopher", philosopher_node)
    workflow.add_node("critic", critic_wrapper)
    workflow.add_node("synthesizer", synthesizer_wrapper)
    workflow.add_node("store", create_store_node(knowledge_store))

    workflow.set_entry_point("librarian")
    workflow.add_edge("librarian", "philosopher")
    workflow.add_edge("philosopher", "critic")

    workflow.add_conditional_edges(
        "critic",
        should_synthesize,
        {"synthesizer": "synthesizer", "store": "store"},
    )
    workflow.add_conditional_edges(
        "synthesizer",
        should_loop_back,
        {"critic": "critic", "store": "store"},
    )

    workflow.add_edge("store", END)

    app = workflow.compile()
    logger.info("Ingestion graph compiled successfully")
    return app
