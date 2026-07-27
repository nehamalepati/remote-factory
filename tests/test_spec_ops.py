"""Tests for factory.spec.ops — validate, scope, update, impact operations."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.spec.ops import (
    _parse_verdict,
    validate_spec,
)
from factory.workflow.definitions import (
    improve_workflow,
    spec_update_workflow,
)
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


# ── Fixtures ────────────────────────────────────────────────────

BASIC_SPEC = """\
# Repo Spec

## Modules

### models
- **Path:** myapp/models.py
- **Role:** Data models
- **Exports:** User, Config
- **Depends on:** none

### store
- **Path:** myapp/store.py
- **Role:** Data persistence
- **Exports:** Store
- **Depends on:** models
"""

FIXTURE_SPEC = """\
# Repo Spec

## Modules

### CLI
**Path:** `factory/cli.py`
**Role:** CLI entry point

### Spec
**Path:** `factory/spec/`
**Role:** Spec generation and validation

### Models
**Path:** `factory/models.py`
**Role:** Domain models
"""

FIXTURE_DIFF = """\
diff --git a/factory/spec/update.py b/factory/spec/update.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/factory/spec/update.py
@@ -0,0 +1,5 @@
+def scope_diff():
+    pass
diff --git a/factory/cli.py b/factory/cli.py
index abc..def 100644
--- a/factory/cli.py
+++ b/factory/cli.py
@@ -1,3 +1,5 @@
 import os
+import sys
diff --git a/factory/old_module.py b/factory/old_module.py
deleted file mode 100644
--- a/factory/old_module.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""

PASS_REPORT = """\
# Spec Validation Report

## Errors
None

## Warnings
- Orphan module: 'utils' has zero consumers

Verdict: PASS
"""

FAIL_REPORT = """\
# Spec Validation Report

## Errors
- Module 'cli': path 'factory/cli.py' does not exist

## Warnings
- Orphan module: 'utils' has zero consumers

Verdict: FAIL
"""

SCOPE_REPORT = """\
## Affected Modules
- CLI
- Spec

## New Files
- factory/spec/update.py

## Deleted Files
- factory/old_module.py
"""


def _write_spec(project: Path, spec_content: str) -> Path:
    spec_path = project / "SPEC.md"
    spec_path.write_text(spec_content)
    return spec_path


def _setup_fixture_project(tmp_path: Path) -> Path:
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "SPEC.md").write_text(FIXTURE_SPEC)
    factory_dir = project / ".factory"
    factory_dir.mkdir()
    exp_dir = factory_dir / "experiments" / "1"
    exp_dir.mkdir(parents=True)
    (exp_dir / "changes.diff").write_text(FIXTURE_DIFF)
    return project


# ── _parse_verdict ──────────────────────────────────────────────


class TestParseVerdict:
    def test_pass(self) -> None:
        assert _parse_verdict("some text\nVerdict: PASS\n") is True

    def test_fail(self) -> None:
        assert _parse_verdict("some text\nVerdict: FAIL\n") is False

    def test_missing_defaults_true(self) -> None:
        assert _parse_verdict("no verdict here") is True

    def test_verdict_mid_text(self) -> None:
        assert _parse_verdict("intro\nVerdict: FAIL\nmore text") is False


# ── validate_spec integration ───────────────────────────────────


class TestValidateSpec:
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(PASS_REPORT, 0)),
    )
    async def test_pass_writes_report(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        _report, is_valid = await validate_spec(tmp_path)
        assert is_valid
        report_path = tmp_path / ".factory" / "spec_validation.md"
        assert report_path.is_file()
        assert "Verdict: PASS" in report_path.read_text()

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(FAIL_REPORT, 0)),
    )
    async def test_fail_verdict(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        _report, is_valid = await validate_spec(tmp_path)
        assert not is_valid

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error occurred", 1)),
    )
    async def test_agent_failure_returns_valid(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        _report, is_valid = await validate_spec(tmp_path)
        assert is_valid

    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await validate_spec(tmp_path)


# ── _get_diff_text ──────────────────────────────────────────────


class TestGetDiffText:
    def test_reads_experiment_diff_file(self, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        exp_dir = tmp_path / ".factory" / "experiments" / "1"
        exp_dir.mkdir(parents=True)
        (exp_dir / "changes.diff").write_text("diff content")

        result = _get_diff_text(tmp_path, experiment_id=1, spec_rel="SPEC.md")
        assert result == "diff content"

    def test_missing_experiment_diff_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        with pytest.raises(FileNotFoundError, match="No diff found"):
            _get_diff_text(tmp_path, experiment_id=99, spec_rel="SPEC.md")

    @patch("factory.spec.ops.subprocess.run")
    def test_git_diff_from_spec_commit(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="diff --git a/x.py b/x.py\n"),
        ]

        result = _get_diff_text(tmp_path, experiment_id=None, spec_rel="SPEC.md")
        assert "diff --git" in result

    @patch("factory.spec.ops.subprocess.run")
    def test_git_diff_fallback_to_head_minus_1(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="fallback diff\n"),
        ]

        result = _get_diff_text(tmp_path, experiment_id=None, spec_rel="SPEC.md")
        assert result == "fallback diff\n"

    @patch("factory.spec.ops.subprocess.run")
    def test_initial_commit_uses_root_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=128, stderr="fatal: bad revision"),
            MagicMock(returncode=0, stdout="root diff\n"),
        ]

        result = _get_diff_text(tmp_path, experiment_id=None, spec_rel="SPEC.md")
        assert result == "root diff\n"
        root_call = mock_run.call_args_list[2]
        assert "--root" in root_call[0][0]

    @patch("factory.spec.ops.subprocess.run")
    def test_root_diff_failure_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=128, stderr="fatal: bad revision"),
            MagicMock(returncode=1, stderr="fatal: unable to read tree"),
        ]

        with pytest.raises(RuntimeError, match="git diff failed"):
            _get_diff_text(tmp_path, experiment_id=None, spec_rel="SPEC.md")

    @patch("factory.spec.ops.subprocess.run")
    def test_git_diff_failure_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from factory.spec.ops import _get_diff_text

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=128, stderr="fatal: bad revision"),
        ]

        with pytest.raises(RuntimeError, match="git diff failed"):
            _get_diff_text(tmp_path, experiment_id=None, spec_rel="SPEC.md")


# ── scope_diff / update_spec ─────────────────────────────────────


class TestScopeDiff:
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(SCOPE_REPORT, 0)),
    )
    async def test_writes_scope_file(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.ops import scope_diff

        project = _setup_fixture_project(tmp_path)
        result = await scope_diff(project, experiment_id=1)

        assert "Affected Modules" in result
        scope_path = project / ".factory" / "spec_update_scope.md"
        assert scope_path.is_file()


class TestUpdateSpec:
    @patch(
        "factory.spec.ops.scope_diff",
        new_callable=lambda: AsyncMock(return_value=SCOPE_REPORT),
    )
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("patched", 0)),
    )
    async def test_patches_spec(
        self, mock_agent: AsyncMock, mock_scope: AsyncMock, tmp_path: Path
    ) -> None:
        from factory.spec.ops import update_spec

        project = _setup_fixture_project(tmp_path)
        result = await update_spec(project)
        assert result == project / "SPEC.md"


class TestScopeDiffErrors:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import scope_diff

        project = tmp_path / "empty_project"
        project.mkdir()

        with pytest.raises(FileNotFoundError):
            await scope_diff(project, experiment_id=1)

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error", 1)),
    )
    async def test_agent_failure_raises(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.ops import scope_diff

        project = _setup_fixture_project(tmp_path)

        with pytest.raises(RuntimeError, match="Scope diff agent failed"):
            await scope_diff(project, experiment_id=1)


class TestUpdateSpecErrors:
    async def test_no_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import update_spec

        with pytest.raises(FileNotFoundError, match="No repo spec"):
            await update_spec(tmp_path)


# ── get_impact ───────────────────────────────────────────────────


class TestGetImpact:
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("## Impact: models\nhub module", 0)),
    )
    async def test_returns_impact_snippet(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.ops import get_impact

        (tmp_path / "SPEC.md").write_text(BASIC_SPEC)
        result = await get_impact("models", tmp_path)
        assert "Impact: models" in result


class TestGetImpactErrors:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        from factory.spec.ops import get_impact

        with pytest.raises(FileNotFoundError):
            await get_impact("models", tmp_path)

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error", 1)),
    )
    async def test_agent_failure_raises(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.spec.ops import get_impact

        (tmp_path / "SPEC.md").write_text(BASIC_SPEC)

        with pytest.raises(RuntimeError, match="Impact analysis agent failed"):
            await get_impact("models", tmp_path)


# ── W₁₀ Spec Update workflow ───────────────────────────────────


class TestSpecUpdateWorkflow:
    def test_validates(self) -> None:
        wf = spec_update_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"spec-update workflow has issues: {issues}"

    def test_name(self) -> None:
        assert spec_update_workflow().name == "spec-update"

    def test_start_node(self) -> None:
        assert spec_update_workflow().start_node == "diff_scope"

    def test_has_required_nodes(self) -> None:
        wf = spec_update_workflow()
        expected = {"diff_scope", "patch", "gate_patch", "revalidate", "gate_revalidate"}
        assert expected == set(wf.nodes.keys())

    def test_diff_scope_is_fn(self) -> None:
        node = spec_update_workflow().nodes["diff_scope"]
        assert isinstance(node, FnNode)
        assert "factory spec scope" in node.command

    def test_patch_is_opus_agent(self) -> None:
        node = spec_update_workflow().nodes["patch"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert node.model == "opus"

    def test_gates_are_ceo(self) -> None:
        wf = spec_update_workflow()
        for gate_id in ("gate_patch", "gate_revalidate"):
            gate = wf.nodes[gate_id]
            assert isinstance(gate, GateNode)
            assert gate.evaluator_role == AgentRole.CEO

    def test_revalidate_is_fn(self) -> None:
        node = spec_update_workflow().nodes["revalidate"]
        assert isinstance(node, FnNode)
        assert "factory spec validate" in node.command

    def test_reloop_from_gate_revalidate_to_patch(self) -> None:
        wf = spec_update_workflow()
        reloop_edges = [
            e for e in wf.edges if e.source == "gate_revalidate" and e.target == "patch"
        ]
        assert len(reloop_edges) == 1


# ── Improve workflow integration ────────────────────────────────


class TestImproveWorkflowSpecUpdate:
    def test_has_spec_update_node(self) -> None:
        assert "spec_update" in improve_workflow().nodes

    def test_spec_update_is_non_blocking_fn(self) -> None:
        node = improve_workflow().nodes["spec_update"]
        assert isinstance(node, FnNode)
        assert node.blocking is False

    def test_archivist_to_spec_update_edge(self) -> None:
        wf = improve_workflow()
        edges = [e for e in wf.edges if e.source == "archivist" and e.target == "spec_update"]
        assert len(edges) == 1

    def test_improve_still_validates(self) -> None:
        issues = improve_workflow().validate_graph()
        assert issues == [], f"improve workflow has issues: {issues}"


# ── CLI spec subcommands ────────────────────────────────────────


class TestCmdSpecGenerate:
    def test_not_a_directory(self) -> None:
        from factory.cli.spec import cmd_spec_generate

        args = argparse.Namespace(path="/nonexistent/path")
        assert cmd_spec_generate(args) == 1

    @patch("factory.spec.generate.generate_spec", new_callable=AsyncMock)
    def test_success(self, mock_gen: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_generate

        spec_path = tmp_path / "SPEC.md"
        mock_gen.return_value = spec_path
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_spec_generate(args) == 0

    @patch(
        "factory.spec.generate.generate_spec",
        new_callable=lambda: AsyncMock(side_effect=ValueError("No source files")),
    )
    def test_error(self, mock_gen: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_generate

        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_spec_generate(args) == 1


class TestCmdSpecValidate:
    def test_no_spec(self, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_validate

        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_spec_validate(args) == 1

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(PASS_REPORT, 0)),
    )
    def test_pass(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_validate

        _write_spec(tmp_path, BASIC_SPEC)
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_spec_validate(args) == 0

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(FAIL_REPORT, 0)),
    )
    def test_fail(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_validate

        _write_spec(tmp_path, BASIC_SPEC)
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_spec_validate(args) == 1


class TestCmdSpecScope:
    def test_no_spec(self, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_scope

        args = argparse.Namespace(path=str(tmp_path), experiment=None)
        assert cmd_spec_scope(args) == 1

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(SCOPE_REPORT, 0)),
    )
    def test_success(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_scope

        project = _setup_fixture_project(tmp_path)
        args = argparse.Namespace(path=str(project), experiment=1)
        assert cmd_spec_scope(args) == 0


class TestCmdSpecUpdate:
    def test_no_spec(self, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_update

        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_spec_update(args) == 1

    @patch(
        "factory.spec.ops.scope_diff",
        new_callable=lambda: AsyncMock(return_value=SCOPE_REPORT),
    )
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("patched", 0)),
    )
    def test_success(self, mock_agent: AsyncMock, mock_scope: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_update

        project = _setup_fixture_project(tmp_path)
        args = argparse.Namespace(path=str(project))
        assert cmd_spec_update(args) == 0


class TestCmdSpecImpact:
    def test_no_spec(self, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_impact

        args = argparse.Namespace(project=str(tmp_path), module="models")
        assert cmd_spec_impact(args) == 1

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("## Impact: models\nhub", 0)),
    )
    def test_success(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        from factory.cli.spec import cmd_spec_impact

        (tmp_path / "SPEC.md").write_text(BASIC_SPEC)
        args = argparse.Namespace(project=str(tmp_path), module="models")
        assert cmd_spec_impact(args) == 0


# ── Graph reference validation ──────────────────────────────────


def _setup_graph(tmp_path: Path, nodes: list[dict], edges: list[dict] | None = None) -> None:
    import json

    gdir = tmp_path / ".factory" / "graphify-out"
    gdir.mkdir(parents=True, exist_ok=True)
    data = {"nodes": nodes, "edges": edges or []}
    (gdir / "graph.json").write_text(json.dumps(data))


class TestValidateGraphReferences:
    def test_returns_empty_when_no_graph(self, tmp_path: Path) -> None:
        from factory.spec.ops import _validate_graph_references

        result = _validate_graph_references("[[graph:Foo]]", tmp_path)
        assert result == ""

    def test_reports_resolved_and_orphans(self, tmp_path: Path) -> None:
        from factory.spec.ops import _validate_graph_references

        _setup_graph(tmp_path, [{"id": "Foo", "type": "class"}])
        spec = "See [[graph:Foo]] and [[graph:Missing]]."
        result = _validate_graph_references(spec, tmp_path)
        assert "Resolved: 1" in result
        assert "Orphans: 1" in result
        assert "Missing" in result

    def test_validates_typed_entity_refs(self, tmp_path: Path) -> None:
        from factory.spec.ops import _validate_graph_references

        _setup_graph(tmp_path, [{"id": "Bar"}])
        spec = "[[graph:entity:Bar]] and [[graph:entity:Gone]]"
        result = _validate_graph_references(spec, tmp_path)
        assert "Resolved: 1" in result
        assert "Gone" in result

    def test_validates_community_refs(self, tmp_path: Path) -> None:
        from factory.spec.ops import _validate_graph_references

        _setup_graph(tmp_path, [{"id": "A", "community": "core"}])
        spec = "[[graph:community:core]] and [[graph:community:missing]]"
        result = _validate_graph_references(spec, tmp_path)
        assert "Resolved: 1" in result
        assert "community:missing" in result

    def test_deduplicates_refs(self, tmp_path: Path) -> None:
        from factory.spec.ops import _validate_graph_references

        _setup_graph(tmp_path, [{"id": "X"}])
        spec = "[[graph:X]] and [[graph:X]] repeated."
        result = _validate_graph_references(spec, tmp_path)
        assert "Resolved: 1" in result
        assert "Orphans: 0" in result

    def test_returns_empty_when_no_refs(self, tmp_path: Path) -> None:
        from factory.spec.ops import _validate_graph_references

        _setup_graph(tmp_path, [{"id": "A"}])
        result = _validate_graph_references("No refs here.", tmp_path)
        assert result == ""


# ── Graph context for diff ──────────────────────────────────────


class TestGraphContextForDiff:
    def test_returns_empty_when_no_graph(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_context_for_diff

        diff = "diff --git a/foo.py b/foo.py\n+++ b/foo.py"
        result = _graph_context_for_diff(diff, tmp_path)
        assert result == ""

    def test_extracts_changed_files_and_neighbors(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_context_for_diff

        _setup_graph(
            tmp_path,
            [{"id": "foo.py"}, {"id": "bar.py"}, {"id": "baz.py"}],
            [{"source": "foo.py", "target": "bar.py"}, {"source": "baz.py", "target": "foo.py"}],
        )
        diff = "diff --git a/foo.py b/foo.py\n+++ b/foo.py\n"
        result = _graph_context_for_diff(diff, tmp_path)
        assert "foo.py" in result
        assert "bar.py" in result
        assert "baz.py" in result

    def test_falls_back_to_stem_lookup(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_context_for_diff

        _setup_graph(
            tmp_path,
            [{"id": "foo"}, {"id": "dep"}],
            [{"source": "foo", "target": "dep"}],
        )
        diff = "diff --git a/src/foo.py b/src/foo.py\n+++ b/src/foo.py\n"
        result = _graph_context_for_diff(diff, tmp_path)
        assert "dep" in result

    def test_returns_empty_for_empty_diff(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_context_for_diff

        _setup_graph(tmp_path, [{"id": "A"}])
        result = _graph_context_for_diff("", tmp_path)
        assert result == ""

    def test_returns_empty_when_no_files_in_graph(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_context_for_diff

        _setup_graph(tmp_path, [{"id": "unrelated"}])
        diff = "diff --git a/other.py b/other.py\n+++ b/other.py\n"
        result = _graph_context_for_diff(diff, tmp_path)
        assert result == ""

    def test_truncates_neighbors_over_10(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_context_for_diff

        neighbors = [{"id": f"dep{i}"} for i in range(12)]
        edges = [{"source": "main.py", "target": f"dep{i}"} for i in range(12)]
        _setup_graph(tmp_path, [{"id": "main.py"}, *neighbors], edges)
        diff = "diff --git a/main.py b/main.py\n+++ b/main.py\n"
        result = _graph_context_for_diff(diff, tmp_path)
        assert "+2 more" in result


# ── Graph impact ────────────────────────────────────────────────


class TestGraphImpact:
    def test_returns_none_when_no_graph(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_impact

        assert _graph_impact("foo", tmp_path) is None

    def test_returns_none_when_node_not_found(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_impact

        _setup_graph(tmp_path, [{"id": "bar"}])
        assert _graph_impact("missing", tmp_path) is None

    def test_basic_impact(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_impact

        _setup_graph(
            tmp_path,
            [
                {"id": "store", "type": "module", "file": "factory/store.py", "community": "core"},
                {"id": "dep1"},
                {"id": "caller1"},
            ],
            [
                {"source": "store", "target": "dep1", "type": "imports"},
                {"source": "caller1", "target": "store", "type": "calls"},
            ],
        )
        result = _graph_impact("store", tmp_path)
        assert result is not None
        assert "## Impact: store" in result
        assert "factory/store.py" in result
        assert "Type: module" in result
        assert "dep1" in result
        assert "imports" in result
        assert "caller1" in result
        assert "**Community:** core" in result
        assert "LOW" in result

    def test_high_severity_with_many_dependents(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_impact

        dependents = [{"id": f"caller{i}"} for i in range(8)]
        edges = [{"source": f"caller{i}", "target": "hub", "type": "calls"} for i in range(8)]
        _setup_graph(tmp_path, [{"id": "hub"}, *dependents], edges)
        result = _graph_impact("hub", tmp_path)
        assert result is not None
        assert "HIGH" in result
        assert "8 direct dependents" in result

    def test_medium_severity(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_impact

        dependents = [{"id": f"c{i}"} for i in range(4)]
        edges = [{"source": f"c{i}", "target": "mid"} for i in range(4)]
        _setup_graph(tmp_path, [{"id": "mid"}, *dependents], edges)
        result = _graph_impact("mid", tmp_path)
        assert result is not None
        assert "MEDIUM" in result

    def test_truncates_long_dep_lists(self, tmp_path: Path) -> None:
        from factory.spec.ops import _graph_impact

        deps = [{"id": f"dep{i}"} for i in range(20)]
        edges = [{"source": "root", "target": f"dep{i}"} for i in range(20)]
        _setup_graph(tmp_path, [{"id": "root"}, *deps], edges)
        result = _graph_impact("root", tmp_path)
        assert result is not None
        assert "… and 5 more" in result
