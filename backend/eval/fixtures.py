"""
Golden fixture loading for the eval harness.

A fixture is a JSON file describing one scenario to run through the real
ingestion (and optionally query) pipeline, plus the expected outcome to score
against. Fixtures live in backend/eval/fixtures/*.json.

Fixture schema
--------------
{
  "id": "unique-case-id",
  "description": "human-readable summary of what this case tests",
  "documents": [
    {"source_label": "doc_a.txt", "raw_content": "..."},
    {"source_label": "doc_b.txt", "raw_content": "..."}
  ],
  "expected_concepts": {
    "doc_a.txt": ["at least one concept about X", "..."]
  },
  "expected_contradiction": {
    // Present only if documents[1] is expected to conflict with documents[0].
    "should_detect": true,
    "min_confidence": 0.5,
    "max_confidence": 1.0
  },
  "query": {
    // Optional — if present, runs the query graph after ingestion.
    "text": "What does the evidence say about X?",
    "expect_citation": true
  }
}

Concept/contradiction expectations are intentionally loose (substring/keyword
checks, confidence ranges) rather than exact-match, because LLM phrasing
varies between runs even at temperature=0. The scorer is grading whether the
pipeline reached the right *kind* of conclusion, not reproducing golden text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class DocumentCase:
    source_label: str
    raw_content: str


@dataclass
class ContradictionExpectation:
    should_detect: bool
    min_confidence: float = 0.0
    max_confidence: float = 1.0


@dataclass
class QueryExpectation:
    text: str
    expect_citation: bool = True


@dataclass
class GoldenCase:
    id: str
    description: str
    documents: List[DocumentCase]
    expected_concepts: Dict[str, List[str]] = field(default_factory=dict)
    expected_contradiction: Optional[ContradictionExpectation] = None
    query: Optional[QueryExpectation] = None

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "GoldenCase":
        documents = [
            DocumentCase(source_label=d["source_label"], raw_content=d["raw_content"])
            for d in raw["documents"]
        ]

        contradiction = None
        if raw.get("expected_contradiction"):
            ec = raw["expected_contradiction"]
            contradiction = ContradictionExpectation(
                should_detect=bool(ec["should_detect"]),
                min_confidence=float(ec.get("min_confidence", 0.0)),
                max_confidence=float(ec.get("max_confidence", 1.0)),
            )

        query = None
        if raw.get("query"):
            q = raw["query"]
            query = QueryExpectation(
                text=q["text"],
                expect_citation=bool(q.get("expect_citation", True)),
            )

        return GoldenCase(
            id=raw["id"],
            description=raw.get("description", ""),
            documents=documents,
            expected_concepts=raw.get("expected_concepts", {}),
            expected_contradiction=contradiction,
            query=query,
        )


def load_fixtures(fixtures_dir: Optional[Path] = None) -> List[GoldenCase]:
    """Load every *.json fixture in the fixtures directory, sorted by filename."""
    directory = fixtures_dir or FIXTURES_DIR
    cases: List[GoldenCase] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text())
        cases.append(GoldenCase.from_dict(raw))
    return cases
