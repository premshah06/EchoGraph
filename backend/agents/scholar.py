"""
Scholar Agent - Answers queries using the knowledge base.
Responsible for semantic search and answer generation with citations.
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


def scholar_node(state: EchoState, knowledge_store: KnowledgeStore) -> EchoState:
    """Answer query text using semantic retrieval and source-aware generation."""
    query = (state.get("query_text") or "").strip()
    logger.info("Scholar agent processing query")

    emit_event(
        state,
        event="agent_start",
        agent="scholar",
        data={"agent": "scholar", "label": "Generating answer"},
    )

    try:
        if not query:
            state["retrieved_nodes"] = []
            state["final_answer"] = "No query text was provided."
            state["current_agent"] = "scholar"
            state["processing_complete"] = True
            return state

        llm_client = get_llm_client()
        query_embedding = llm_client.embed_text(query)

        candidates = knowledge_store.search_similar(query_embedding, top_k=10, threshold=0.0)
        synthesized = [node for node in candidates if node.get("node_type") == "synthesized"]
        bridge = [node for node in candidates if node.get("node_type") == "bridge"]
        raw = [node for node in candidates if node.get("node_type") == "raw"]

        retrieved_nodes = (synthesized + bridge + raw)[:5]

        if not retrieved_nodes:
            answer = "I don't have enough information in the knowledge base to answer this question yet."
            state["retrieved_nodes"] = []
            state["final_answer"] = answer
            state["current_agent"] = "scholar"
            state["processing_complete"] = True
            emit_event(
                state,
                event="scholar_answer",
                agent="scholar",
                data={"answer": answer, "sources": []},
            )
            return state

        context_blocks = []
        source_ids: List[str] = []
        for node in retrieved_nodes:
            source_ids.append(node["id"])
            context_blocks.append(
                "\n".join(
                    [
                        f"Node ID: {node['id']}",
                        f"Concept: {node['concept']}",
                        f"Summary: {node['summary']}",
                        f"Source: {node['source']}",
                        f"Type: {node['node_type']}",
                    ]
                )
            )

        context_blob = "\n\n".join(context_blocks)

        answer_prompt = f"""
You answer questions using only the provided knowledge nodes. Reason through the evidence
step by step before writing your final answer.

QUESTION:
{query}

KNOWLEDGE NODES:
{context_blob}

Think through these steps before answering:
1. Relevance: Which nodes directly address the question? Which are only tangentially related?
2. Priority: Synthesized nodes represent resolved knowledge — prefer them over raw nodes
   when they address the same topic.
3. Evidence: What do the most relevant nodes collectively support as an answer?
4. Gaps and uncertainty: Is the evidence complete, or are there important caveats?
   If knowledge is limited, say so honestly rather than overreaching.
5. Answer: Compose a clear, grounded response that reflects the weight of evidence.

Requirements:
- Cite supporting nodes inline using exact format: According to node #[<full-node-id>] ...
- Prioritize synthesized nodes over raw nodes when addressing the same claim.
- Use at least one citation when evidence exists.
- If evidence is conflicting or incomplete, acknowledge it explicitly in your answer.
"""

        def _on_token(chunk: str) -> None:
            emit_event(
                state,
                event="agent_token",
                agent="scholar",
                data={"token": chunk},
            )

        final_answer = with_retry(
            llm_client.invoke_streaming, answer_prompt, "scholar", _on_token, agent="scholar"
        ).strip()
        if not final_answer:
            final_answer = "I could not generate a grounded answer from the retrieved knowledge nodes."

        for node_id in source_ids:
            knowledge_store.update_retrieval_count(node_id)

        state["retrieved_nodes"] = retrieved_nodes
        state["final_answer"] = final_answer
        state["current_agent"] = "scholar"
        state["processing_complete"] = True

        emit_event(
            state,
            event="scholar_answer",
            agent="scholar",
            data={"answer": final_answer, "sources": source_ids},
        )

        return state

    except Exception:
        logger.exception("Error in scholar_node")
        emit_event(
            state,
            event="error",
            agent="scholar",
            data={"message": "Query answering failed"},
        )
        raise
