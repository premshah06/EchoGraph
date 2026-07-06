"""
Scores a CaseResult (from runner.py) against its GoldenCase expectations.

Grading philosophy: concept-recall checks are keyword/substring matches
against expected themes (LLM phrasing varies run to run, so exact match would
be meaningless). Contradiction and confidence checks are the harder signal —
they validate that Critic/Synthesizer reached the right *kind* of conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from backend.eval.fixtures import GoldenCase
from backend.eval.runner import CaseResult


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ScoredCase:
    case_id: str
    description: str
    is_smoke_test_only: bool
    checks: List[CheckResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return all(check.passed for check in self.checks)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)


def _score_concepts(case: GoldenCase, result: CaseResult) -> List[CheckResult]:
    checks: List[CheckResult] = []
    results_by_source = {dr.source_label: dr.concepts for dr in result.document_results}

    for source_label, expected_keywords in case.expected_concepts.items():
        concepts = results_by_source.get(source_label, [])
        concepts_blob = " ".join(concepts).lower()

        for keyword in expected_keywords:
            found = keyword.lower() in concepts_blob
            checks.append(
                CheckResult(
                    name=f"concept_recall[{source_label}]:'{keyword}'",
                    passed=found,
                    detail=(
                        f"expected a concept mentioning '{keyword}' for {source_label}, "
                        f"got: {concepts!r}"
                    ),
                )
            )

    return checks


def _score_contradiction(case: GoldenCase, result: CaseResult) -> List[CheckResult]:
    if case.expected_contradiction is None:
        return []

    expected = case.expected_contradiction
    checks: List[CheckResult] = []

    detected = bool(result.contradiction_found)
    checks.append(
        CheckResult(
            name="contradiction_detected",
            passed=detected == expected.should_detect,
            detail=f"expected should_detect={expected.should_detect}, got contradiction_found={detected}",
        )
    )

    if expected.should_detect and detected:
        confidence = result.resolution_confidence
        in_range = confidence is not None and expected.min_confidence <= confidence <= expected.max_confidence
        checks.append(
            CheckResult(
                name="resolution_confidence_in_range",
                passed=in_range,
                detail=(
                    f"expected confidence in [{expected.min_confidence}, {expected.max_confidence}], "
                    f"got {confidence}"
                ),
            )
        )

    return checks


def _score_query(case: GoldenCase, result: CaseResult) -> List[CheckResult]:
    if case.query is None:
        return []

    checks: List[CheckResult] = []
    answer = result.query_answer or ""

    checks.append(
        CheckResult(
            name="query_answered",
            passed=bool(answer.strip()),
            detail=f"expected a non-empty answer, got: {answer!r}",
        )
    )

    if case.query.expect_citation:
        has_citation = "node #" in answer.lower() or "[node" in answer.lower()
        checks.append(
            CheckResult(
                name="query_has_citation",
                passed=has_citation,
                detail=f"expected an inline node citation, got: {answer!r}",
            )
        )

    return checks


def score_case(case: GoldenCase, result: CaseResult) -> ScoredCase:
    if result.error:
        return ScoredCase(
            case_id=case.id,
            description=case.description,
            is_smoke_test_only=result.is_smoke_test_only,
            error=result.error,
        )

    checks = (
        _score_concepts(case, result)
        + _score_contradiction(case, result)
        + _score_query(case, result)
    )

    return ScoredCase(
        case_id=case.id,
        description=case.description,
        is_smoke_test_only=result.is_smoke_test_only,
        checks=checks,
    )
