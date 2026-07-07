"""Unit tests for the LLM-as-judge confidence auditor."""

from __future__ import annotations

from typing import List

from backend.audit.confidence_auditor import audit_node, run_audit


class FakeJudgeLLM:
    """Returns a scripted CONFIDENCE:/REASONING: response, ignoring the prompt."""

    def __init__(self, confidence: float = 0.5, reasoning: str = "test reasoning"):
        self.confidence = confidence
        self.reasoning = reasoning
        self.calls = 0

    def invoke(self, _prompt: str) -> str:
        self.calls += 1
        return f"CONFIDENCE: {self.confidence}\nREASONING: {self.reasoning}"


class FakeStore:
    def __init__(self, nodes):
        self._nodes = {n["id"]: n for n in nodes}

    def get_all_nodes(self):
        return list(self._nodes.values())

    def get_node(self, node_id: str):
        return self._nodes.get(node_id)


def _synth_node(node_id: str, confidence: float, derivation_source_ids: List[str]):
    return {
        "id": node_id,
        "concept": f"Synthesis for {node_id}",
        "summary": "A synthesized resolution summary.",
        "node_type": "synthesized",
        "confidence": confidence,
        "derivation": {
            "source_node_ids": derivation_source_ids,
            "contradiction_reason": "Claims conflict on the same metric",
            "credibility_assessment": "Source A is more credible",
            "synthesis_reasoning": "Both hold under different conditions",
            "loop_iteration": 0,
        },
    }


def _raw_node(node_id: str):
    return {
        "id": node_id,
        "concept": "Original Claim",
        "summary": "The original pre-existing claim.",
        "source": "original-source.txt",
        "node_type": "raw",
        "confidence": 1.0,
        "derivation": None,
    }


class TestAuditNode:
    def test_returns_none_without_derivation(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(),
        )
        node = {"id": "n1", "concept": "X", "confidence": 0.7, "derivation": None}
        store = FakeStore([node])

        result = audit_node(node, store)

        assert result is None

    def test_computes_gap_and_direction_overconfident(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.3),
        )
        source = _raw_node("src-1")
        node = _synth_node("syn-1", confidence=0.9, derivation_source_ids=["src-1"])
        store = FakeStore([source, node])

        result = audit_node(node, store)

        assert result is not None
        assert result.synthesizer_confidence == 0.9
        assert result.judge_confidence == 0.3
        assert result.direction == "overconfident"
        assert result.is_miscalibrated is True

    def test_computes_gap_and_direction_underconfident(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.9),
        )
        source = _raw_node("src-1")
        node = _synth_node("syn-1", confidence=0.3, derivation_source_ids=["src-1"])
        store = FakeStore([source, node])

        result = audit_node(node, store)

        assert result.direction == "underconfident"
        assert result.is_miscalibrated is True

    def test_well_calibrated_when_gap_small(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.72),
        )
        source = _raw_node("src-1")
        node = _synth_node("syn-1", confidence=0.70, derivation_source_ids=["src-1"])
        store = FakeStore([source, node])

        result = audit_node(node, store)

        assert result.direction == "well_calibrated"
        assert result.is_miscalibrated is False

    def test_handles_missing_source_node_gracefully(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.5),
        )
        node = _synth_node("syn-1", confidence=0.5, derivation_source_ids=["does-not-exist"])
        store = FakeStore([node])

        result = audit_node(node, store)

        assert result is not None


class TestRunAudit:
    def test_audits_only_synthesized_nodes_with_derivation(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.6),
        )
        raw = _raw_node("raw-1")
        synth = _synth_node("syn-1", confidence=0.6, derivation_source_ids=["raw-1"])
        store = FakeStore([raw, synth])

        summary = run_audit(store)

        assert summary["audited_count"] == 1
        assert summary["results"][0]["node_id"] == "syn-1"

    def test_respects_sample_size(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.6),
        )
        nodes = [_raw_node("raw-1")]
        for i in range(5):
            nodes.append(_synth_node(f"syn-{i}", confidence=0.6, derivation_source_ids=["raw-1"]))
        store = FakeStore(nodes)

        summary = run_audit(store, sample_size=2)

        assert summary["audited_count"] == 2

    def test_empty_when_no_synthesized_nodes(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(),
        )
        store = FakeStore([_raw_node("raw-1")])

        summary = run_audit(store)

        assert summary["audited_count"] == 0
        assert summary["mean_gap"] == 0.0
        assert summary["results"] == []

    def test_reports_most_overconfident_node(self, monkeypatch):
        judge = FakeJudgeLLM()

        def judge_factory():
            return judge

        monkeypatch.setattr("backend.audit.confidence_auditor.get_llm_client", judge_factory)

        raw = _raw_node("raw-1")
        mild = _synth_node("syn-mild", confidence=0.6, derivation_source_ids=["raw-1"])
        severe = _synth_node("syn-severe", confidence=0.95, derivation_source_ids=["raw-1"])
        store = FakeStore([raw, mild, severe])

        # Judge always says 0.3 regardless of node — severe (0.95) has the
        # bigger gap and should be flagged as most overconfident.
        judge.confidence = 0.3

        summary = run_audit(store)

        assert summary["most_overconfident_node_id"] == "syn-severe"

    def test_aggregate_mean_gap_is_averaged_correctly(self, monkeypatch):
        monkeypatch.setattr(
            "backend.audit.confidence_auditor.get_llm_client",
            lambda: FakeJudgeLLM(confidence=0.5),
        )
        raw = _raw_node("raw-1")
        node_a = _synth_node("syn-a", confidence=0.5, derivation_source_ids=["raw-1"])  # gap 0.0
        node_b = _synth_node("syn-b", confidence=0.7, derivation_source_ids=["raw-1"])  # gap 0.2
        store = FakeStore([raw, node_a, node_b])

        summary = run_audit(store)

        assert summary["mean_gap"] == 0.1
