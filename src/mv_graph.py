"""Refresh order, derived from user_dependencies - never from MV names.

The names suggest MV_00_* -> MV_01_* -> MV_02_*, and they are wrong in
three places (docs/MV_STATE.md section 3): two MV_00_* read other MVs, and
MV_00_JOIN_PMC_LEFTJOIN is two levels up. An MV refreshed before its inputs
is rebuilt from their previous contents with no error, which is how the
February refresh left MVs UNUSABLE. So the order is computed, and a cycle
is fatal.
"""

import os

# One line per fact, pipe-separated, so sqlplus column formatting and line
# wrapping cannot corrupt a name. DEP rows cover every reference an MV has;
# MV rows list every MV, so an MV with no MV parent still appears.
DEPENDENCY_SQL = """
SELECT 'MV|' || mview_name FROM user_mviews;
SELECT 'DEP|' || name || '|' || referenced_name || '|' || referenced_type
FROM   user_dependencies
WHERE  type = 'MATERIALIZED VIEW';
"""

MATERIALIZED_VIEW = "MATERIALIZED VIEW"


class CycleError(ValueError):
    pass


def parse_dependency_output(lines):
    """Parse DEPENDENCY_SQL output into (mv_names, dependency_rows)."""
    mv_names, rows = set(), []
    for line in lines:
        parts = line.strip().split("|")
        if parts[0] == "MV" and len(parts) == 2:
            mv_names.add(parts[1])
        elif parts[0] == "DEP" and len(parts) == 4:
            rows.append((parts[1], parts[2], parts[3]))
    return mv_names, rows


def load_fixture(path):
    """Read a captured user_dependencies TSV (see src/tests/fixtures)."""
    rows = []
    with open(path) as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            name, referenced_name, referenced_type = line.rstrip("\n").split("\t")
            rows.append((name, referenced_name, referenced_type))
    return {row[0] for row in rows}, rows


def build_graph(mv_names, rows):
    """Map each MV to the set of MVs it reads.

    Only references of type MATERIALIZED VIEW count; every MV also depends on
    its own container TABLE and on base tables, which do not order anything.
    """
    graph = {name: set() for name in mv_names}
    for name, referenced_name, referenced_type in rows:
        if (
            referenced_type == MATERIALIZED_VIEW
            and name in graph
            and referenced_name in graph
            and referenced_name != name
        ):
            graph[name].add(referenced_name)
    return graph


def levels(graph):
    """Group MVs into levels: level n reads only MVs of levels below n.

    Alphabetical within a level, so the order is deterministic and a diff of
    two plans shows real changes only.
    """
    remaining = {name: set(parents) for name, parents in graph.items()}
    placed, result = set(), []
    while remaining:
        ready = sorted(name for name, parents in remaining.items() if parents <= placed)
        if not ready:
            raise CycleError(
                "materialized views depend on each other in a cycle: "
                + ", ".join(sorted(remaining))
            )
        result.append(ready)
        placed.update(ready)
        for name in ready:
            del remaining[name]
    return result


def refresh_order(graph):
    """[(order_no, level_no, mv_name), ...], parents always before children."""
    order, position = [], 0
    for level_no, names in enumerate(levels(graph)):
        for name in names:
            position += 1
            order.append((position, level_no, name))
    return order


def descendants(graph, name):
    """Every MV that reads `name`, directly or through other MVs."""
    children = {mv: {c for c, parents in graph.items() if mv in parents} for mv in graph}
    found, stack = set(), [name]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures")
