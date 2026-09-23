"""Refresh stage entry point.

Dry run is the default and touches no database at all: the dependency graph
comes from a captured fixture, and every statement --execute would send is
printed instead of sent.

    uv run python src/run_refresh.py                      # dry run, full chain
    uv run python src/run_refresh.py --only MV_NUM_PUB    # dry run, one MV
    uv run python src/run_refresh.py --execute            # real run - approval

--execute reads the graph live from user_dependencies, so it refreshes what
the database holds, not what the fixture remembers.
"""

import argparse
import os
import sys

import mv_graph
import refresh
from db import run_sqlplus
from settings import Settings

DEFAULT_FIXTURE = os.path.join(mv_graph.FIXTURE_DIR, "mv_dependencies_2026-09-23.tsv")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="actually refresh (default: print the plan and SQL only)")
    parser.add_argument("--only", metavar="MV",
                        help="refresh this one MV instead of the whole chain")
    parser.add_argument("--deps-file", default=DEFAULT_FIXTURE,
                        help="dependency fixture for a dry run (ignored with --execute)")
    return parser.parse_args(argv)


def live_graph(dsn, runner=None):
    kwargs = {} if runner is None else {"runner": runner}
    lines = run_sqlplus(mv_graph.DEPENDENCY_SQL, dsn, **kwargs).lines
    mv_names, rows = mv_graph.parse_dependency_output(lines)
    if not mv_names:
        raise RuntimeError("no materialized views returned: " + " / ".join(lines)[-500:])
    return mv_graph.build_graph(mv_names, rows)


def select_steps(graph, only=None):
    order = mv_graph.refresh_order(graph)
    if only:
        order = [entry for entry in order if entry[2] == only.upper()]
        if not order:
            raise SystemExit(f"{only} is not a materialized view in the graph")
    return refresh.steps_from_order(order)


def main(argv=None, env=None, runner=None, out=print):
    args = parse_args(argv)
    settings = Settings(env)

    if args.execute:
        dsn = settings.oracle_dsn
        graph = live_graph(dsn, runner)
        source = "live user_dependencies"
    else:
        graph = mv_graph.build_graph(*mv_graph.load_fixture(args.deps_file))
        source = args.deps_file

    steps = select_steps(graph, args.only)
    out(f"graph: {source}")
    out(f"{'order':>5} {'level':>5}  materialized view")
    for step in steps:
        out(f"{step.order_no:>5} {step.level_no:>5}  {step.mv_name}")
    if args.only:
        stale = sorted(mv_graph.descendants(graph, args.only.upper()))
        if stale:
            out(f"note: refreshing {args.only} alone leaves {len(stale)} MVs that read it "
                f"behind it: {', '.join(stale)}")

    if not args.execute:
        out("\n-- DRY RUN: the statements --execute would send, in order. None sent.")
        out(refresh.plan_sql(steps, settings.slurm_job_id))
        return 0

    succeeded, _ = refresh.execute(steps, dsn, out, settings.slurm_job_id, runner)
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
