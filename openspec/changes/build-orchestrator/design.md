# Design

## Context

Three facts from the warehouse inventory
(`../epmc_pipeline/db_inventory/`) shape everything here:

1. **25 MVs, all `DEMAND` + `COMPLETE` refresh.** The largest,
   `MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY`, is 106,896,482 rows / 5.7 GB. There
   is no fast refresh anywhere, despite an `MLOG$_ENA_SEQUENCES` existing.
2. **They are layered.** Names suggest `MV_00_*` → `MV_01_*` → `MV_02_*`, and
   `MV_00_JOIN_ENA_PMC` (66M rows) is itself read by later MVs. Refreshing in
   the wrong order produces internally inconsistent results that no error
   reports.
3. **They join both sources.** `MV_00_JOIN_ENA_PMC` is the clearest case.

## Decisions

### Halt on any failure

If either harvest fails: no dump, no refresh, alert, MVs keep their last good
state.

Considered and rejected: refreshing with whatever loaded. It produces a
dashboard where ENA counts are newer than EPMC counts with nothing indicating
it — worse than stale, because it is wrong while appearing current. A
stale dashboard is at least uniformly stale.

The Slurm chain gives this for free: `--dependency=afterok` never starts a
dependent job whose predecessor failed.

### `atomic_refresh=TRUE`

`FALSE` is substantially faster — truncate plus direct-path insert — but the
MV is **empty while it rebuilds**, and APEX users would see blank charts for
the duration. On a 106.9M-row MV that window is long.

`TRUE` keeps every MV readable and consistent throughout at the cost of time
and undo. Since the refresh runs unattended, off-hours, behind a dependency
chain, time is the cheap resource here and user-visible emptiness is not.

Revisit only if a measured complete refresh proves impractically long — in
which case the better answer is fast refresh (see Open Questions), not
`FALSE`.

### Slurm dependency chain, not one job

Considered: a single sbatch running all four stages.

Rejected because one wall-clock limit would have to cover the worst case of
everything, a failure in the last stage wastes the whole allocation, and
restarting one stage means rerunning all of them. Separate jobs also give
each stage its own memory request — the harvests are network-bound and small,
the refresh is I/O- and undo-heavy.

The cost is that state lives in Slurm's job graph rather than in one process.
Acceptable: each pipeline already records its own outcome in its own ledger.

### ENA and EPMC in parallel

They share no state and write different tables. `dump` depends on
`afterok:<ena>:<epmc>`, so either failing still halts the chain, while wall
clock is the max of the two rather than the sum.

### Refresh order derived, not assumed

The naming convention is evidence, not authority. Build the order by
topologically sorting `user_dependencies` where `type = 'MATERIALIZED VIEW'`,
and fail loudly on a cycle. An MV refreshed before its inputs yields stale
results with no error — the failure mode this project exists to prevent.

## Risks

**[Risk]** A complete refresh of the whole chain may exceed any sensible
window. → **Mitigation**: measure one large MV first, with approval, before
committing to a schedule. Do not guess.

**[Risk]** The 15 INVALID MVs may fail to compile for reasons unrelated to
this work. → **Mitigation**: recompile them as a separate, prior step. Do not
fold pre-existing breakage into the first orchestrated run, or the run's
outcome will be unreadable.

**[Accepted]** The orchestrator has no ledger of its own initially; each
pipeline records its own. If a dashboard later needs one view of a whole
cycle, `V_PIPELINE_EXECUTIONS`
(`../epmc_pipeline/src/schema/pipeline_executions_view.sql`, written and not
applied) is the intended shape.

## Open Questions

- **Dump scope.** Check for an existing RMAN or expdp schedule first; the
  stage may be redundant. Otherwise: pipeline tables only, full schema, or
  tables plus DDL without MV contents.
- **Fast refresh.** An unused `MLOG$_ENA_SEQUENCES` suggests someone intended
  it. If the base tables support it, it changes the economics of this whole
  project and should be settled before optimising a complete refresh.
- **Schedule.** The two pipelines currently start at 09:17 and 17:00 with
  `--begin` dates from 2023. The chain replaces that; when should it start?
