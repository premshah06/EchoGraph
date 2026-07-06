"""
Headless pipeline runner for the eval harness.

Invokes the real ingestion_graph/query_graph (same code path as the FastAPI
/ingest and /query endpoints) against a fresh, temporary KnowledgeStore — no
HTTP server involved. Each GoldenCase's documents are ingested in order, then
scored against its expectations.

Whether this exercises real model reasoning or just structural wiring depends
entirely on which LLM client get_llm_client() resolves to:
  - Demo mode / no OPENAI_API_KEY → DemoLLMClient (scripted responses).
    Results are labeled "smoke test only" — they prove the pipeline runs
    end-to-end without crashing, NOT that the reasoning is correct, since
    DemoLLMClient's contradiction detector is a hardcoded "no" by default.
  - Real OpenAI key configured → actual reasoning is scored for real.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import get_settings
from backend.eval.fixtures import GoldenCase
from backend.graphs.ingestion_graph import create_ingestion_graph
from backend.graphs.query_graph import create_query_graph
from backend.knowledge_store import KnowledgeStore


def _base_state(input_type: str, raw_content: str = "", source_label: str = "", query_text: str = "") -> Dict[str, Any]:
    return {
        "input_type": input_type,
        "raw_content": raw_content,
        "source_label": source_label,
        "existing_nodes": [],
        "new_concepts": [],
        "contradictions": [],
        "connections": [],
        "resolutions": [],
        "query_text": query_text,
        "retrieved_nodes": [],
        "final_answer": "",
        "current_agent": "",
        "processing_complete": False,
        "contradiction_found": False,
        "resolution_confidence": 1.0,
        "loop_count": 0,
        "session_id": "eval",
        "agent_events": [],
        "event_callback": lambda _event: None,
    }


@dataclass
class DocumentResult:
    source_label: str
    concepts: List[str]


@dataclass
class CaseResult:
    case_id: str
    description: str
    is_smoke_test_only: bool
    document_results: List[DocumentResult] = field(default_factory=list)
    contradiction_found: Optional[bool] = None
    resolution_confidence: Optional[float] = None
    query_answer: Optional[str] = None
    error: Optional[str] = None


def run_case(case: GoldenCase) -> CaseResult:
    """Run one golden case through the real ingestion (+ optional query) graph."""
    settings = get_settings()
    is_smoke_test_only = settings.demo_mode or not settings.is_openai_configured

    tmp_dir = tempfile.mkdtemp(prefix=f"echograph_eval_{case.id}_")
    try:
        store = KnowledgeStore(persist_directory=tmp_dir)
        ingestion_graph = create_ingestion_graph(store)

        document_results: List[DocumentResult] = []
        last_result: Dict[str, Any] = {}

        for doc in case.documents:
            state = _base_state(
                input_type="document",
                raw_content=doc.raw_content,
                source_label=doc.source_label,
            )
            last_result = ingestion_graph.invoke(state)
            concepts = [c["concept"] for c in last_result.get("new_concepts", [])]
            document_results.append(DocumentResult(source_label=doc.source_label, concepts=concepts))

        result = CaseResult(
            case_id=case.id,
            description=case.description,
            is_smoke_test_only=is_smoke_test_only,
            document_results=document_results,
            contradiction_found=last_result.get("contradiction_found"),
            resolution_confidence=last_result.get("resolution_confidence"),
        )

        if case.query is not None:
            query_graph = create_query_graph(store)
            query_state = _base_state(input_type="query", query_text=case.query.text)
            query_result = query_graph.invoke(query_state)
            result.query_answer = query_result.get("final_answer")

        return result
    except Exception as exc:  # noqa: BLE001 - surfaced in the scorecard, not raised
        return CaseResult(
            case_id=case.id,
            description=case.description,
            is_smoke_test_only=is_smoke_test_only,
            error=str(exc),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
