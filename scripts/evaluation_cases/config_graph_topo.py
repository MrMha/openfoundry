import copy

import pytest
from config_graph.core import dependency_order


def test_empty_and_isolated_lexicographical_order() -> None:
    assert dependency_order({}) == []
    assert dependency_order({"c": [], "a": [], "b": []}) == ["a", "b", "c"]


def test_deterministic_tie_breaking_and_diamond_graph() -> None:
    graph = {"web": ["api", "auth"], "api": ["db"], "auth": ["db"], "db": [], "cache": []}
    assert dependency_order(graph) == ["cache", "db", "api", "auth", "web"]


def test_duplicate_dependencies_and_no_input_mutation() -> None:
    graph = {"b": ["a", "a", "a"], "a": []}
    snapshot = copy.deepcopy(graph)
    assert dependency_order(graph) == ["a", "b"]
    assert graph == snapshot


@pytest.mark.parametrize(
    "bad_graph",
    [
        {"a": ["missing"]},
        {"a": ["a"]},
        {"a": ["b"], "b": ["a"]},
        {"a": ["b"], "b": ["c"], "c": ["a"], "d": []},
    ],
)
def test_unknown_dependency_and_cycles_raise_value_error(bad_graph: dict[str, list[str]]) -> None:
    with pytest.raises(ValueError):
        dependency_order(bad_graph)


def test_deep_1200_node_chain_runs_iteratively() -> None:
    n = 1200
    graph = {f"n{i:04d}": ([f"n{i - 1:04d}"] if i > 0 else []) for i in range(n)}
    order = dependency_order(graph)
    assert order == [f"n{i:04d}" for i in range(n)]


def test_exhaustive_small_dags_against_permutation_oracle() -> None:
    from itertools import permutations

    nodes = ["z", "a", "m", "b"]
    edges = [(nodes[i], nodes[j]) for i in range(4) for j in range(i)]
    for mask in range(1 << len(edges)):
        graph = {node: [] for node in nodes}
        for bit, (node, dependency) in enumerate(edges):
            if mask & (1 << bit):
                graph[node].append(dependency)
        valid = [
            order
            for order in permutations(nodes)
            if all(
                order.index(dep) < order.index(node) for node, deps in graph.items() for dep in deps
            )
        ]
        assert dependency_order(graph) == list(min(valid))
