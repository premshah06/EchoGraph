"""
CLI entrypoint for the agent evaluation harness.

Usage
-----
    python -m backend.eval.run

Runs every golden fixture in backend/eval/fixtures/ through the real
ingestion/query graphs and prints a scorecard. Exits non-zero if any case
fails AND at least one case ran with real model reasoning (not smoke-test-only) —
smoke-test-only failures are reported but don't fail the run, since a scripted
demo response is not a meaningful correctness signal.
"""

from __future__ import annotations

import sys

from backend.eval.fixtures import load_fixtures
from backend.eval.runner import run_case
from backend.eval.scorer import ScoredCase, score_case


def _print_case(scored: ScoredCase) -> None:
    label = "SMOKE TEST ONLY (demo mode / no API key)" if scored.is_smoke_test_only else "EVALUATED"
    status = "ERROR" if scored.error else ("PASS" if scored.passed else "FAIL")

    print(f"\n[{status}] {scored.case_id}  ({label})")
    print(f"  {scored.description}")

    if scored.error:
        print(f"  ERROR: {scored.error}")
        return

    print(f"  {scored.pass_count}/{scored.total_count} checks passed")
    for check in scored.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"    [{mark}] {check.name}")
        if not check.passed:
            print(f"           {check.detail}")


def main() -> int:
    cases = load_fixtures()
    if not cases:
        print("No fixtures found in backend/eval/fixtures/.")
        return 1

    scored_cases = []
    for case in cases:
        result = run_case(case)
        scored = score_case(case, result)
        scored_cases.append(scored)
        _print_case(scored)

    real_evals = [s for s in scored_cases if not s.is_smoke_test_only]
    smoke_tests = [s for s in scored_cases if s.is_smoke_test_only]

    total_pass = sum(1 for s in scored_cases if s.passed)
    print(f"\n{'=' * 60}")
    print(f"Scorecard: {total_pass}/{len(scored_cases)} cases passed")
    if smoke_tests:
        smoke_pass = sum(1 for s in smoke_tests if s.passed)
        print(f"  {len(smoke_tests)} case(s) ran smoke-test-only (demo mode): {smoke_pass}/{len(smoke_tests)} passed")
        print("  Smoke-test results validate pipeline wiring only, not reasoning quality.")
    if real_evals:
        real_pass = sum(1 for s in real_evals if s.passed)
        print(f"  {len(real_evals)} case(s) ran against real model reasoning: {real_pass}/{len(real_evals)} passed")

    # Only fail the run on real-eval failures — smoke-test failures don't
    # indicate a reasoning regression since DemoLLMClient's responses are
    # scripted and don't attempt to satisfy fixture expectations.
    if real_evals and any(not s.passed for s in real_evals):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
