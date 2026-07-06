"""Unit tests for the eval harness's fixture loading and scoring logic."""

from __future__ import annotations

from backend.eval.fixtures import GoldenCase, load_fixtures
from backend.eval.runner import CaseResult, DocumentResult
from backend.eval.scorer import score_case


class TestFixtureLoading:
    def test_loads_bundled_fixtures(self):
        cases = load_fixtures()
        ids = {c.id for c in cases}
        assert "contradiction_case" in ids
        assert "no_contradiction_case" in ids

    def test_contradiction_case_has_expectation(self):
        cases = {c.id: c for c in load_fixtures()}
        case = cases["contradiction_case"]
        assert case.expected_contradiction is not None
        assert case.expected_contradiction.should_detect is True

    def test_no_contradiction_case_expects_no_detection(self):
        cases = {c.id: c for c in load_fixtures()}
        case = cases["no_contradiction_case"]
        assert case.expected_contradiction.should_detect is False


def make_case(**overrides) -> GoldenCase:
    from backend.eval.fixtures import ContradictionExpectation, DocumentCase, QueryExpectation

    defaults = dict(
        id="test-case",
        description="test",
        documents=[DocumentCase(source_label="doc.txt", raw_content="content")],
        expected_concepts={"doc.txt": ["alpha"]},
        expected_contradiction=ContradictionExpectation(should_detect=True, min_confidence=0.5, max_confidence=1.0),
        query=QueryExpectation(text="a question?", expect_citation=True),
    )
    defaults.update(overrides)
    return GoldenCase(**defaults)


class TestScorer:
    def test_concept_recall_passes_on_substring_match(self):
        case = make_case(expected_contradiction=None, query=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            document_results=[DocumentResult(source_label="doc.txt", concepts=["Alpha Particle Physics"])],
        )
        scored = score_case(case, result)
        assert scored.passed

    def test_concept_recall_fails_when_missing(self):
        case = make_case(expected_contradiction=None, query=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            document_results=[DocumentResult(source_label="doc.txt", concepts=["Something unrelated"])],
        )
        scored = score_case(case, result)
        assert not scored.passed

    def test_contradiction_detection_match_passes(self):
        case = make_case(expected_concepts={}, query=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            contradiction_found=True,
            resolution_confidence=0.7,
        )
        scored = score_case(case, result)
        assert scored.passed

    def test_contradiction_confidence_out_of_range_fails(self):
        case = make_case(expected_concepts={}, query=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            contradiction_found=True,
            resolution_confidence=0.1,  # below min_confidence=0.5
        )
        scored = score_case(case, result)
        assert not scored.passed

    def test_contradiction_mismatch_fails(self):
        case = make_case(expected_concepts={}, query=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            contradiction_found=False,
        )
        scored = score_case(case, result)
        assert not scored.passed

    def test_query_citation_check_passes_with_node_reference(self):
        case = make_case(expected_concepts={}, expected_contradiction=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            query_answer="According to node #abc123, the answer is yes.",
        )
        scored = score_case(case, result)
        assert scored.passed

    def test_query_citation_check_fails_without_reference(self):
        case = make_case(expected_concepts={}, expected_contradiction=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            query_answer="The answer is yes, generically.",
        )
        scored = score_case(case, result)
        assert not scored.passed

    def test_error_result_fails_regardless_of_checks(self):
        case = make_case()
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=False,
            error="boom",
        )
        scored = score_case(case, result)
        assert not scored.passed
        assert scored.error == "boom"

    def test_smoke_test_flag_propagates(self):
        case = make_case(expected_concepts={}, expected_contradiction=None, query=None)
        result = CaseResult(
            case_id="test-case",
            description="test",
            is_smoke_test_only=True,
        )
        scored = score_case(case, result)
        assert scored.is_smoke_test_only is True
