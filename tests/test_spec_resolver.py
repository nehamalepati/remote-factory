"""Tests for factory.spec.resolver — graph reference resolution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx

from factory.spec.resolver import (
    _find_node,
    load_graph,
    resolve_community,
    resolve_entity,
    resolve_path,
    resolve_query,
    resolve_references,
)


def _make_graph(
    *nodes: tuple[str, dict], edges: list[tuple[str, str, dict]] | None = None
) -> nx.DiGraph:
    G = nx.DiGraph()
    for node_id, attrs in nodes:
        G.add_node(node_id, **attrs)
    for src, tgt, attrs in edges or []:
        G.add_edge(src, tgt, **attrs)
    return G


def _write_graph_json(tmp_path: Path, data: dict) -> None:
    gdir = tmp_path / ".factory" / "graphify-out"
    gdir.mkdir(parents=True)
    (gdir / "graph.json").write_text(json.dumps(data))


class TestLoadGraph:
    def test_returns_digraph(self, tmp_path: Path) -> None:
        data = {
            "nodes": [{"id": "A", "type": "module"}, {"id": "B", "type": "class"}],
            "edges": [{"source": "A", "target": "B", "type": "imports"}],
        }
        _write_graph_json(tmp_path, data)
        G = load_graph(tmp_path)
        assert G is not None
        assert G.number_of_nodes() == 2
        assert G.number_of_edges() == 1

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert load_graph(tmp_path) is None

    def test_uses_name_fallback_for_node_id(self, tmp_path: Path) -> None:
        data = {"nodes": [{"name": "foo"}], "edges": []}
        _write_graph_json(tmp_path, data)
        G = load_graph(tmp_path)
        assert G is not None
        assert "foo" in G

    def test_uses_links_key_for_edges(self, tmp_path: Path) -> None:
        data = {
            "nodes": [{"id": "X"}, {"id": "Y"}],
            "links": [{"from": "X", "to": "Y"}],
        }
        _write_graph_json(tmp_path, data)
        G = load_graph(tmp_path)
        assert G is not None
        assert G.has_edge("X", "Y")

    def test_skips_nodes_without_id(self, tmp_path: Path) -> None:
        data = {"nodes": [{"type": "orphan"}, {"id": "A"}], "edges": []}
        _write_graph_json(tmp_path, data)
        G = load_graph(tmp_path)
        assert G is not None
        assert G.number_of_nodes() == 1

    def test_skips_edges_without_endpoints(self, tmp_path: Path) -> None:
        data = {
            "nodes": [{"id": "A"}],
            "edges": [{"source": "A", "target": ""}, {"source": "", "target": "A"}],
        }
        _write_graph_json(tmp_path, data)
        G = load_graph(tmp_path)
        assert G is not None
        assert G.number_of_edges() == 0


class TestFindNode:
    def test_exact_match(self) -> None:
        G = _make_graph(("factory.store", {}))
        assert _find_node("factory.store", G) == "factory.store"

    def test_dot_to_underscore(self) -> None:
        G = _make_graph(("factory_store", {}))
        assert _find_node("factory.store", G) == "factory_store"

    def test_case_insensitive_underscore(self) -> None:
        G = _make_graph(("factory_store", {}))
        assert _find_node("Factory.Store", G) == "factory_store"

    def test_suffix_match_dot(self) -> None:
        G = _make_graph(("factory.spec.resolver", {}))
        assert _find_node("resolver", G) == "factory.spec.resolver"

    def test_suffix_match_slash(self) -> None:
        G = _make_graph(("factory/spec/resolver", {}))
        assert _find_node("resolver", G) == "factory/spec/resolver"

    def test_case_insensitive_full(self) -> None:
        G = _make_graph(("FactoryStore", {}))
        assert _find_node("factorystore", G) == "FactoryStore"

    def test_returns_none_when_not_found(self) -> None:
        G = _make_graph(("foo", {}))
        assert _find_node("bar", G) is None


class TestResolveEntity:
    def test_found_with_attributes(self) -> None:
        G = _make_graph(
            ("mod.Foo", {"type": "class", "file": "mod.py", "line": 10, "community": "core"}),
            ("mod.bar", {}),
            edges=[("mod.Foo", "mod.bar", {})],
        )
        result = resolve_entity("mod.Foo", G)
        assert "**mod.Foo**" in result
        assert "Type: class" in result
        assert "mod.py:10" in result
        assert "Community: core" in result
        assert "1 outgoing" in result

    def test_not_found(self) -> None:
        G = _make_graph()
        result = resolve_entity("missing", G)
        assert "not found" in result

    def test_file_without_line(self) -> None:
        G = _make_graph(("A", {"file": "a.py"}))
        result = resolve_entity("A", G)
        assert "a.py" in result
        assert ":" not in result.split("Location: ")[1].split(" |")[0]

    def test_uses_kind_and_path_fallbacks(self) -> None:
        G = _make_graph(("X", {"kind": "function", "path": "x.py"}))
        result = resolve_entity("X", G)
        assert "Type: function" in result
        assert "x.py" in result


class TestResolvePath:
    def test_forward_path(self) -> None:
        G = _make_graph(("A", {}), ("B", {}), ("C", {}), edges=[("A", "B", {}), ("B", "C", {})])
        result = resolve_path("A", "C", G)
        assert result == "A → B → C"

    def test_reverse_path(self) -> None:
        G = _make_graph(("A", {}), ("B", {}), edges=[("B", "A", {})])
        result = resolve_path("A", "B", G)
        assert "reverse direction" in result
        assert "B → A" in result

    def test_no_path(self) -> None:
        G = _make_graph(("A", {}), ("B", {}))
        result = resolve_path("A", "B", G)
        assert "No path" in result

    def test_source_not_found(self) -> None:
        G = _make_graph(("B", {}))
        result = resolve_path("A", "B", G)
        assert "Source entity" in result

    def test_target_not_found(self) -> None:
        G = _make_graph(("A", {}))
        result = resolve_path("A", "B", G)
        assert "Target entity" in result


class TestResolveQuery:
    @patch("factory.spec.resolver.subprocess.run")
    def test_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="query result\n")
        result = resolve_query("what calls foo", tmp_path)
        assert result == "query result"

    @patch("factory.spec.resolver.subprocess.run")
    def test_no_results(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = resolve_query("nothing", tmp_path)
        assert "returned no results" in result

    @patch("factory.spec.resolver.subprocess.run", side_effect=FileNotFoundError)
    def test_graphify_not_available(self, _mock: MagicMock, tmp_path: Path) -> None:
        result = resolve_query("test", tmp_path)
        assert "not available" in result

    @patch("factory.spec.resolver.subprocess.run")
    def test_uses_double_dash_separator(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        resolve_query("--malicious", tmp_path)
        cmd = mock_run.call_args[0][0]
        dash_idx = cmd.index("--")
        assert cmd[dash_idx + 1] == "--malicious"


class TestResolveCommunity:
    def test_found_by_community_attr(self) -> None:
        G = _make_graph(
            ("A", {"community": "core"}),
            ("B", {"community": "core"}),
            ("C", {"community": "utils"}),
        )
        result = resolve_community("core", G)
        assert "A" in result
        assert "B" in result
        assert "C" not in result

    def test_case_insensitive(self) -> None:
        G = _make_graph(("A", {"community": "Core"}))
        result = resolve_community("core", G)
        assert "A" in result

    def test_uses_group_fallback(self) -> None:
        G = _make_graph(("X", {"group": "infra"}))
        result = resolve_community("infra", G)
        assert "X" in result

    def test_falls_back_to_name_substring(self) -> None:
        G = _make_graph(("core.engine", {}), ("utils.helper", {}))
        result = resolve_community("core", G)
        assert "core.engine" in result
        assert "utils.helper" not in result

    def test_not_found(self) -> None:
        G = _make_graph(("A", {}))
        result = resolve_community("nonexistent", G)
        assert "not found" in result

    def test_truncates_large_communities(self) -> None:
        nodes = [(f"node{i}", {"community": "big"}) for i in range(25)]
        G = _make_graph(*nodes)
        result = resolve_community("big", G)
        assert "… and 5 more" in result


class TestResolveReferences:
    def test_resolves_bare_ref(self, tmp_path: Path) -> None:
        data = {
            "nodes": [{"id": "factory.store", "type": "module"}],
            "edges": [],
        }
        _write_graph_json(tmp_path, data)
        text = "See [[graph:factory.store]] for details."
        result = resolve_references(text, tmp_path)
        assert "**factory.store**" in result
        assert "[[graph:" not in result

    def test_resolves_entity_ref(self, tmp_path: Path) -> None:
        data = {"nodes": [{"id": "Foo", "type": "class"}], "edges": []}
        _write_graph_json(tmp_path, data)
        text = "See [[graph:entity:Foo]] for the class."
        result = resolve_references(text, tmp_path)
        assert "**Foo**" in result

    def test_preserves_unresolvable_refs(self, tmp_path: Path) -> None:
        data = {"nodes": [], "edges": []}
        _write_graph_json(tmp_path, data)
        text = "See [[graph:entity:Missing]]."
        result = resolve_references(text, tmp_path)
        assert "not found" in result

    def test_no_graph_returns_unchanged(self, tmp_path: Path) -> None:
        text = "See [[graph:entity:Foo]] and [[graph:Bar]]."
        result = resolve_references(text, tmp_path)
        assert result == text

    def test_resolves_path_ref(self, tmp_path: Path) -> None:
        data = {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"source": "A", "target": "B"}],
        }
        _write_graph_json(tmp_path, data)
        text = "Path: [[graph:path:A:B]]"
        result = resolve_references(text, tmp_path)
        assert "A → B" in result

    def test_resolves_community_ref(self, tmp_path: Path) -> None:
        data = {"nodes": [{"id": "X", "community": "infra"}], "edges": []}
        _write_graph_json(tmp_path, data)
        text = "See [[graph:community:infra]]."
        result = resolve_references(text, tmp_path)
        assert "X" in result
