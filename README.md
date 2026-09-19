# WiLDSi warehouse orchestrator

Runs the weekly warehouse update end to end: both harvest pipelines, then a
database dump, then a materialized-view refresh — and stops at the first
failure rather than publishing a half-updated picture.

**Status: not started.** This repository currently contains the brief, the
design decisions already taken, and the open questions. See
`openspec/changes/build-orchestrator/` for the plan and
`CLAUDE.md` for the working rules.

## What it orchestrates

```
  ena_pipeline  ─┐
                 ├─ afterok(both) ─→  dump  ─ afterok ─→  MV refresh
  epmc_pipeline ─┘
```

The two harvests are independent and run in parallel. Everything downstream
depends on both succeeding.

| stage | lives in |
|---|---|
| ENA harvest | `../ena_pipeline` |
| EPMC harvest | `../epmc_pipeline` |
| dump | here (scope not yet decided) |
| MV refresh | here |

## Why it stops on failure

The materialized views join ENA and EPMC data. Refreshing when only one side
updated publishes a picture that looks complete and is not: the APEX
dashboard would show ENA counts newer than EPMC counts, with nothing
indicating it. A stale-but-consistent dashboard is preferable to a
fresh-but-skewed one.

## Prerequisites to understand before starting

- `../epmc_pipeline/CLAUDE.md` and `../ena_pipeline/CLAUDE.md` — the two
  pipelines' own rules.
- `../epmc_pipeline/docs/IMPROVEMENTS.md` — what was rebuilt and why,
  including the warehouse defects found by measurement.
- `../epmc_pipeline/db_inventory/` — a read-only inventory of the warehouse
  (tables, MVs, dependency graph, refresh state), produced by
  `../epmc_pipeline/tools/inspect_db.sh`.
