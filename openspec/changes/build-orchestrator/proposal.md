# Build the warehouse orchestrator

## Why

The weekly warehouse update is currently four things that do not know about
each other. Two pipelines run on independent Slurm schedules
(`ena_job` at 09:17, `epmc_download…` at 17:00, both `--begin` dates from
2023). The database dump is not automated at all. The materialized-view
refresh is manual — and has not happened since **February 2026**, while the
pipelines have loaded weekly ever since.

The measurable consequence is in the warehouse today:

| MV staleness | count |
|---|---|
| `NEEDS_COMPILE` | 30 |
| `FRESH` | 7 |
| `STALE` | 4 |
| `UNUSABLE` | 2 |

15 MVs are `INVALID`. The APEX application has been serving ~7-month-old
aggregates over continuously-updated tables.

A second problem is subtler and is the reason the stages must be coupled
rather than merely scheduled: the MVs **join ENA and EPMC data**. Refreshing
after only one harvest succeeded publishes a picture that looks complete and
is not.

## What Changes

- **ADDED** a Slurm dependency chain: ENA and EPMC in parallel → dump →
  MV refresh, with `--dependency=afterok` so any failure halts everything
  downstream.
- **ADDED** a dependency-ordered MV refresh using `atomic_refresh=TRUE`,
  with the order derived from `user_dependencies` rather than from MV names.
- **ADDED** a dump stage. **Scope deliberately undecided** — see design.
- **ADDED** run reporting, reusing the notification shape both pipelines
  already have.
- **NOT CHANGED** the two pipelines themselves. The orchestrator invokes
  their existing entry points; it does not reach inside them.

## Capabilities

`orch-scheduling`, `orch-failure-policy`, `orch-dump`, `orch-mv-refresh`,
`orch-reporting`.

## Impact

- The APEX application gets aggregates that match the tables beneath them.
- A failed harvest becomes visible within one cycle instead of silently
  leaving the dashboard stale.
- Risk concentrated in one place: this project issues a full refresh of a
  106.9M-row MV that a live application reads. Hence `atomic_refresh=TRUE`
  and hence the halt-on-failure policy.
