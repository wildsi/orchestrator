import os

import pytest

import mv_graph

FIXTURE = os.path.join(mv_graph.FIXTURE_DIR, "mv_dependencies_2026-09-23.tsv")


@pytest.fixture(scope="module")
def graph():
    return mv_graph.build_graph(*mv_graph.load_fixture(FIXTURE))


def test_the_fixture_holds_all_25_materialized_views(graph):
    assert len(graph) == 25


def test_levels_match_the_order_derived_by_hand_in_mv_state(graph):
    """docs/MV_STATE.md section 3, derived from the same inventory."""
    assert mv_graph.levels(graph) == [
        [
            "MV_00_JOIN_COUNTRY_ENA", "MV_ACC_PRI_LIT", "MV_ACC_PRI_OR_SEC_LIT_01",
            "MV_ACC_PRI_OR_SEC_LIT_02", "MV_ACC_PRI_OR_SEC_LIT_03", "MV_ACC_SEC_LIT_01",
            "MV_ACC_SEC_LIT_02", "MV_NUM_AUTHORS", "MV_NUM_AUTHORS_COUNTRY", "MV_NUM_PUB",
            "MV_PMC_WITH_ANNOTATIONS", "MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS",
            "ZZ_MV_HISTOGRAM_PAT_COUNT_MD5",
        ],
        [
            "MV_00_JOIN_COUNTRY_PMC", "MV_00_JOIN_ENA_PMC", "MV_ACC_PRI_OR_SEC_LIT",
            "MV_ACC_SEC_LIT",
        ],
        [
            "MV_00_JOIN_PMC_LEFTJOIN", "MV_01_DSI_ALL_PUBLICATIONS", "MV_01_IN_COUNTRY_USE",
            "MV_01_JOIN_ENA_LEFTJOIN", "MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY",
            "MV_01_PROVIDING_TO_Y_COUNTRIES", "MV_01_USING_FROM_X_COUNTRIES",
            "MV_ACC_IN_PRIMARY_AND_SECONDARY_LIT",
        ],
    ]


def test_names_are_not_the_order(graph):
    """Three MV_00_* are not level 0 - the reason the order is derived."""
    level_of = {mv: level for _, level, mv in mv_graph.refresh_order(graph)}
    assert level_of["MV_00_JOIN_COUNTRY_PMC"] == 1
    assert level_of["MV_00_JOIN_ENA_PMC"] == 1
    assert level_of["MV_00_JOIN_PMC_LEFTJOIN"] == 2


def test_every_mv_refreshes_after_every_mv_it_reads(graph):
    position = {mv: order_no for order_no, _, mv in mv_graph.refresh_order(graph)}
    for mv, parents in graph.items():
        for parent in parents:
            assert position[parent] < position[mv], (parent, mv)


def test_only_materialized_view_references_order_anything():
    rows = [
        ("A", "A", "TABLE"),                    # its own container
        ("A", "B", "TABLE"),                    # B's container table: not an edge
        ("A", "PMC_REFERENCES", "TABLE"),       # base table
        ("B", "B", "MATERIALIZED VIEW"),        # self-reference
    ]
    assert mv_graph.build_graph({"A", "B"}, rows) == {"A": set(), "B": set()}


def test_a_cycle_is_fatal():
    graph = {"A": {"B"}, "B": {"C"}, "C": {"A"}, "D": set()}
    with pytest.raises(mv_graph.CycleError, match="A, B, C"):
        mv_graph.levels(graph)


def test_descendants_follow_the_chain_transitively(graph):
    assert mv_graph.descendants(graph, "MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS") == {
        "MV_00_JOIN_COUNTRY_PMC", "MV_00_JOIN_PMC_LEFTJOIN", "MV_01_DSI_ALL_PUBLICATIONS",
        "MV_01_IN_COUNTRY_USE", "MV_01_JOIN_ENA_LEFTJOIN",
        "MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY", "MV_01_PROVIDING_TO_Y_COUNTRIES",
        "MV_01_USING_FROM_X_COUNTRIES",
    }
    assert mv_graph.descendants(graph, "MV_NUM_PUB") == set()


def test_live_output_parses_to_the_same_graph_as_the_fixture(graph):
    mv_names, rows = mv_graph.load_fixture(FIXTURE)
    lines = [f"MV|{name}" for name in sorted(mv_names)]
    lines += [f"DEP|{a}|{b}|{t}" for a, b, t in rows]
    lines += ["", "some sqlplus noise"]
    assert mv_graph.build_graph(*mv_graph.parse_dependency_output(lines)) == graph
