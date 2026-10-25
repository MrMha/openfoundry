Use only local SPEC.md for research with zero external web dependencies. Deliver at most 3 source files (`config_graph/__init__.py`, `config_graph/core.py`), unit tests in `tests/`, `README.md`, QA verification, and a launch draft.
Exact signature and behavior in `config_graph/core.py` (and re-exported in `config_graph/__init__.py`):
- `def dependency_order(graph: dict[str, list[str]]) -> list[str]`
- `graph[node]` lists prerequisites that must appear before `node` in the returned ordering.
- At each step, select the lexicographically smallest available node whose prerequisites are all already placed.
- Do not mutate `graph` or its inner dependency lists.
- Accept duplicate prerequisites inside `graph[node]` as a single dependency.
- Raise `ValueError` if any prerequisite is not a key in `graph`, if `graph` contains a self-loop or cycle, or if keys/deps are blank non-strings.
- Implement iteratively so chains of 1,200+ nodes complete without `RecursionError`.
