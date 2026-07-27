"""Tests for factory.spec — graph summary and spec generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.spec.generate import (
    build_graph_summary,
    generate_spec,
)
from factory.workflow.definitions import register_all, spec_generate_workflow
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


# ── Graph summary ───────────────────────────────────────────────


class TestBuildGraphSummary:
    def test_empty_graph(self) -> None:
        summary = build_graph_summary({"nodes": [], "edges": []})
        assert "0 nodes" in summary
        assert "0 edges" in summary

    def test_includes_entity_counts(self) -> None:
        graph = {
            "nodes": [
                {"id": "mod_foo", "label": "Foo", "source_file": "mod.py"},
                {"id": "mod_bar", "label": "bar()", "source_file": "mod.py"},
                {"id": "mod_baz", "label": "baz()", "source_file": "mod.py"},
            ],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "function: 2" in summary
        assert "class: 1" in summary

    def test_includes_relationship_counts(self) -> None:
        graph = {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "edges": [
                {"source": "A", "target": "B", "type": "imports"},
                {"source": "B", "target": "A", "type": "imports"},
            ],
        }
        summary = build_graph_summary(graph)
        assert "imports: 2" in summary

    def test_groups_by_community(self) -> None:
        graph = {
            "nodes": [
                {"name": "Foo", "type": "class", "community": "core"},
                {"name": "Bar", "type": "class", "community": "utils"},
            ],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "Community core" in summary
        assert "Community utils" in summary

    def test_ungrouped_nodes(self) -> None:
        graph = {
            "nodes": [{"name": "Foo", "type": "class"}],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "Ungrouped" in summary

    def test_includes_relationships(self) -> None:
        graph = {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "edges": [{"source": "A", "target": "B", "type": "calls"}],
        }
        summary = build_graph_summary(graph)
        assert "A --[calls]--> B" in summary

    def test_uses_links_key_fallback(self) -> None:
        graph = {
            "nodes": [{"name": "X"}],
            "links": [{"source": "X", "target": "Y", "type": "imports"}],
        }
        summary = build_graph_summary(graph)
        assert "1 edges" in summary
        assert "X --[imports]--> Y" in summary

    def test_uses_group_key_fallback(self) -> None:
        graph = {
            "nodes": [{"name": "Foo", "type": "class", "group": "infra"}],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "Community infra" in summary

    def test_truncation_at_char_limit(self) -> None:
        nodes = [
            {"name": f"Entity{i}", "type": "class", "community": f"comm{i}"} for i in range(500)
        ]
        graph = {"nodes": nodes, "edges": []}
        summary = build_graph_summary(graph, char_limit=2000)
        assert len(summary) < 3000
        assert "truncated" in summary


# ── W₉ Spec Generate workflow ───────────────────────────────────


class TestSpecGenerateWorkflow:
    def test_validates(self) -> None:
        wf = spec_generate_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"spec-generate workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = spec_generate_workflow()
        assert wf.name == "spec-generate"

    def test_start_node(self) -> None:
        wf = spec_generate_workflow()
        assert wf.start_node == "extract"

    def test_no_trigger(self) -> None:
        wf = spec_generate_workflow()
        assert wf.trigger is None

    def test_has_required_nodes(self) -> None:
        wf = spec_generate_workflow()
        expected = {
            "extract",
            "gate_extract",
            "annotate",
            "gate_annotate",
            "validate",
            "gate_validate",
        }
        assert expected == set(wf.nodes.keys())

    def test_extract_is_fn(self) -> None:
        wf = spec_generate_workflow()
        extract = wf.nodes["extract"]
        assert isinstance(extract, FnNode)
        assert "factory graph extract" in extract.command

    def test_annotate_is_researcher(self) -> None:
        wf = spec_generate_workflow()
        annotate = wf.nodes["annotate"]
        assert isinstance(annotate, AgentNode)
        assert annotate.role == AgentRole.RESEARCHER

    def test_gates_are_ceo(self) -> None:
        wf = spec_generate_workflow()
        for gate_id in ("gate_extract", "gate_annotate", "gate_validate"):
            gate = wf.nodes[gate_id]
            assert isinstance(gate, GateNode)
            assert gate.evaluator_type == "agent"
            assert gate.evaluator_role == AgentRole.CEO

    def test_validate_is_fn(self) -> None:
        wf = spec_generate_workflow()
        node = wf.nodes["validate"]
        assert isinstance(node, FnNode)
        assert "factory spec validate" in node.command

    def test_extract_writes_graph(self) -> None:
        wf = spec_generate_workflow()
        extract = wf.nodes["extract"]
        assert ".factory/graphify-out/graph.json" in extract.writes

    def test_annotate_writes_repo_spec(self) -> None:
        wf = spec_generate_workflow()
        annotate = wf.nodes["annotate"]
        assert "SPEC.md" in annotate.writes


# ── Registry includes W₉ ────────────────────────────────────────


class TestRegistryIncludesSpec:
    def test_register_all_includes_spec_generate(self) -> None:
        all_wf = register_all()
        assert "spec-generate" in all_wf

    def test_register_all_count(self) -> None:
        all_wf = register_all()
        assert len(all_wf) == 23

    def test_all_workflows_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"


# ── generate_spec (graph path) ──────────────────────────────────


class TestGenerateSpecGraph:
    async def test_graph_path_success(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        repo_spec = tmp_path / "SPEC.md"
        graph_data = {
            "nodes": [{"name": "main", "type": "module"}],
            "edges": [],
        }

        async def mock_invoke(role, task, project, **kwargs):
            repo_spec.write_text("# Repo spec from graph")
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            result = await generate_spec(tmp_path)

        assert result == repo_spec
        assert "graph" in repo_spec.read_text().lower() or repo_spec.exists()

    async def test_graph_path_passes_summary_to_agent(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        repo_spec = tmp_path / "SPEC.md"
        graph_data = {
            "nodes": [
                {"name": "Engine", "type": "class", "community": "core"},
                {"name": "run", "type": "function", "community": "core"},
            ],
            "edges": [{"source": "Engine", "target": "run", "type": "calls"}],
        }
        captured_tasks: list[str] = []

        async def mock_invoke(role, task, project, **kwargs):
            captured_tasks.append(task)
            repo_spec.write_text("# SPEC")
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            await generate_spec(tmp_path)

        assert len(captured_tasks) == 1
        assert "Code Knowledge Graph Summary" in captured_tasks[0]
        assert "Engine" in captured_tasks[0]

    async def test_single_agent_invocation(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        repo_spec = tmp_path / "SPEC.md"
        graph_data = {"nodes": [{"name": "main", "type": "module"}], "edges": []}
        invoke_calls: list[dict] = []

        async def mock_invoke(role, task, project, **kwargs):
            invoke_calls.append({"role": role, "model": kwargs.get("model")})
            repo_spec.write_text("# SPEC")
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            await generate_spec(tmp_path)

        assert len(invoke_calls) == 1
        assert invoke_calls[0]["model"] is None

    async def test_graph_annotation_failure_raises(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        graph_data = {"nodes": [], "edges": []}

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch(
                "factory.agents.runner.invoke_agent",
                new_callable=lambda: AsyncMock(return_value=("error", 1)),
            ),
        ):
            with pytest.raises(RuntimeError, match="Spec annotation failed"):
                await generate_spec(tmp_path)

    async def test_graph_missing_spec_raises(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        graph_data = {"nodes": [], "edges": []}

        async def mock_invoke(role, task, project, **kwargs):
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            with pytest.raises(FileNotFoundError, match="SPEC"):
                await generate_spec(tmp_path)


# ── generate_spec (graphify pipeline errors) ─────────────────────


class TestGenerateSpecErrors:
    async def test_graphify_not_installed_raises(self, tmp_path: Path) -> None:
        with patch("factory.graph.is_graphify_installed", return_value=False):
            with pytest.raises(RuntimeError, match="graphify is required"):
                await generate_spec(tmp_path)

    async def test_extract_graph_failure_raises(self, tmp_path: Path) -> None:
        with (
            patch("factory.graph.is_graphify_installed", return_value=True),
            patch("factory.graph.extract_graph", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="graphify extraction failed"):
                await generate_spec(tmp_path)

    async def test_load_graph_failure_raises(self, tmp_path: Path) -> None:
        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="graph.json is unreadable"):
                await generate_spec(tmp_path)

    async def test_annotation_failure_raises(self, tmp_path: Path) -> None:
        graph_data = {"nodes": [{"id": "a", "label": "a.py"}], "edges": []}

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch(
                "factory.agents.runner.invoke_agent",
                new_callable=lambda: AsyncMock(return_value=("error", 1)),
            ),
        ):
            with pytest.raises(RuntimeError, match="Spec annotation failed"):
                await generate_spec(tmp_path)

    async def test_missing_spec_after_annotation_raises(self, tmp_path: Path) -> None:
        graph_data = {"nodes": [{"id": "a", "label": "a.py"}], "edges": []}

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch(
                "factory.agents.runner.invoke_agent",
                new_callable=lambda: AsyncMock(return_value=("ok", 0)),
            ),
        ):
            with pytest.raises(FileNotFoundError, match="SPEC"):
                await generate_spec(tmp_path)
