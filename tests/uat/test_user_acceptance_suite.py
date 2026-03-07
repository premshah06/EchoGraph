"""User acceptance coverage for demo content and UX-critical contracts."""

from __future__ import annotations

from pathlib import Path


def test_demo_documents_exist_and_non_empty():
    demo_docs_dir = Path("demo/documents")
    docs = list(demo_docs_dir.glob("*.txt"))
    assert docs, "Expected demo text documents"
    for doc in docs:
        assert doc.read_text(encoding="utf-8").strip(), f"Demo doc is empty: {doc}"


def test_demo_queries_exist_and_non_empty():
    query_file = Path("demo/queries/sample_queries.md")
    assert query_file.exists(), "Missing demo query file"
    assert query_file.read_text(encoding="utf-8").strip()


def test_launch_docs_exist():
    assert Path("docs/deployment-checklist.md").exists()
    assert Path("docs/launch-runbook.md").exists()
    assert Path("docs/final-review.md").exists()


def test_architecture_doc_exists_and_has_diagrams():
    architecture_file = Path("ARCHITECTURE.md")
    assert architecture_file.exists()
    content = architecture_file.read_text(encoding="utf-8")
    assert "```mermaid" in content
    assert "Agent Pipeline" in content
