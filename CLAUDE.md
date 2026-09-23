# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Working in this repository

This project does not exist yet beyond this brief. Read all of it before
writing code.

## Never touch the database without asking

No `sqlplus`, `sqlldr`, `expdp`, TRUNCATE, MERGE, INSERT, DDL or
`DBMS_MVIEW.REFRESH` against `oda-x10-dbs01/projects.ipk-gatersleben.de` —
not even a `SELECT count(*)` — without explicit, per-action approval from the
user. Write the SQL, show it, and stop. Approval for one statement does not
carry to the next.

This matters more here than in the pipelines: this project's entire job is to
issue database operations, and one of them is a full refresh of a 106.9M-row
materialized view that a live application reads.

The read-only alternative: `../epmc_pipeline/db_inventory/` holds a captured
snapshot of the warehouse (tables, MVs, dependency graph, definitions, invalid
objects, refresh state). Answer questions from those files. Re-capturing them
means running `../epmc_pipeline/tools/inspect_db.sh`, which hits the database
and therefore needs approval like anything else.

## The APEX application is the constraint

~25 materialized views back the charts, tables and plots of an Oracle APEX
web app. They exist because the underlying tables hold tens of millions of
rows. Anything that leaves an MV empty, invalid, or inconsistent with its
siblings is visible to users immediately.

Before proposing any operation against an MV, read
`../epmc_pipeline/db_inventory/03_mv_dependencies.txt` and
`04_table_to_dependents.txt`.

## What this orchestrates, and where each stage lives

```
  ena_pipeline  ─┐
                 ├─ afterok(both) ─→  dump  ─ afterok ─→  MV refresh
  epmc_pipeline ─┘
```

| stage | lives in | state |
|---|---|---|
| ENA harvest | `../ena_pipeline` | exists, own sbatch schedule |
| EPMC harvest | `../epmc_pipeline` | exists, own sbatch schedule |
| dump | `../database_dump` — see below | exists, standalone |
| MV refresh | here | manual, last run February 2026 |

The orchestrator invokes the pipelines' existing entry points
(`../{ena,epmc}_pipeline/src/main.py`). It does not reach inside them. Both
are expected to return non-zero on failure — verify that, do not assume it.

`../ena-pipeline` (hyphen) is the superseded predecessor of `../ena_pipeline`
(underscore). Do not read the hyphenated one for current behaviour.

## Repository layout

The refresh stage exists (2026-09-23); the rest does not yet.
`src/mv_graph.py` derives the order, `src/refresh.py` refreshes and logs,
`src/run_refresh.py` is the entry point (dry run by default),
`src/schema/extend_mv_refresh_log.sql` is the log extension it needs
(applied 2026-09-23). `docs/` holds the MV findings (`MV_STATE.md`) and the log design
(`REFRESH_LOG.md`). `openspec/changes/build-orchestrator/` is the plan of record —
`proposal.md` (why), `design.md` (decisions and rejected alternatives),
`tasks.md` (ordered work, phases 0–2 touch no database). Keep them current as
work proceeds; they are also the raw material for the publication.

## Commands

No build exists yet. When scaffolding, inherit the sibling pipelines' setup
(`../epmc_pipeline/pyproject.toml` is the model: uv, flat `src/` on
`pythonpath`, pytest, ruff at line-length 100):

```bash
uv sync                          # create/refresh .venv from pyproject + uv.lock
uv run pytest                    # full suite; must pass with no DB and no network
uv run pytest src/tests/test_mv_graph.py::test_cycle_fails   # a single test
uv run ruff check src
```

Slurm is the runtime, not a local process. The chain is submitted, not run:

```bash
ENA=$(sbatch --parsable  ena.sbatch)
EPMC=$(sbatch --parsable epmc.sbatch)
DUMP=$(sbatch --parsable --dependency=afterok:$ENA:$EPMC dump.sbatch)
sbatch --dependency=afterok:$DUMP refresh.sbatch
```

Anything that issues SQL must have a dry-run mode that prints every statement
it would run and issues none. Use it as the default way to demonstrate
behaviour, so that showing your work does not require approval.

## Decisions already taken — do not relitigate without reason

1. **Halt on any failure.** If either harvest fails, skip the dump and the
   refresh, alert, and leave the MVs on their last good state.
2. **`atomic_refresh=TRUE`.** The MV stays readable and consistent
   throughout. Slower and heavier on undo than `FALSE`, but `FALSE`
   truncates first, so APEX users would see empty charts mid-refresh.
3. **Slurm dependency chain**, not one long job. Each stage is its own
   sbatch with its own resources, wall clock and logs; `--dependency=afterok`
   stops the chain natively; a single stage can be rerun without repeating
   the others.
4. **ENA and EPMC run in parallel.** They are independent. The dump depends
   on `afterok` of both, so either failing still halts everything downstream.

## Still open

- **Dump scope.** `../database_dump/` already implements a dump and is not
  what the brief assumed: it exports a fixed list of tables to CSV,
  compresses them, uploads them to an FTP server over SSH and archives old
  ones by year (`database_dump.sh`, with `--dry-run`; submitted via
  `upload.sbatch`, which needs `--auks=yes` for Kerberos and
  `module load oracle-instant-client`). That is a **publication** of data,
  not a backup. So the question splits in two: is there a separate RMAN or
  expdp backup (ask the DBA), and should this stage wrap the existing
  `database_dump.sh` rather than write a new one? Read that script and its
  README before deciding.
- **MV refresh order.** The `MV_00_*` → `MV_01_*` → `MV_02_*` naming implies a
  layered chain that must refresh bottom-up. **Derive it from
  `user_dependencies`, not from the names.**
- **The 15 INVALID MVs.** Recompiled 2026-09-23, all VALID, no data
  touched. That exposed **10 of 25 MVs as UNUSABLE** (not a consistent
  snapshot; possibly empty or partial). Cause not established; the lead is
  a non-atomic `REFRESH_ALL_MVIEWS`. The derived refresh order is there
  too. See `docs/MV_STATE.md` before any refresh.
- **A weekly scheduler job that does nothing.** `REFRESH_MV_COUNTRY_ENA`
  fires every 7 days, but its PL/SQL block is empty (runs take 0.01 s). No
  MV carries its own `NEXT` schedule. It does not race this chain;
  dropping it is housekeeping. `docs/MV_STATE.md` §5.
- **Fast refresh.** All MVs are `DEMAND` + `COMPLETE`, yet an
  `MLOG$_ENA_SEQUENCES` exists and is unused. Worth investigating: a fast
  refresh would change the cost of this project entirely.
- **Schedule.** The pipelines currently trigger at 09:17 and 17:00 from
  `--begin` dates set in 2023. The chain replaces those; the start time is
  undecided, and the existing triggers are retired only at the end.

## Conventions, inherited from the sibling pipelines

- **Environments**: one `pyproject.toml` + `uv.lock`. No `requirements.txt`.
- **Credentials**: never hardcoded. Resolution order is
  `<PREFIX>_ORACLE_DSN` → first line of `<PREFIX>_ORACLE_CREDENTIALS_FILE` →
  hard failure. Copy `../epmc_pipeline/legacy_db_env.sh`, which also locates
  the Oracle client when `module` is unavailable. The credential goes on
  sqlplus stdin or into a chmod-600 temp file — **never argv**, where `ps`
  exposes it.
- **Character set**: any sqlldr control file must declare
  `characterset AL32UTF8`. Omitting it corrupted ~12% of affiliations in the
  existing warehouse; see `../epmc_pipeline/docs/IMPROVEMENTS.md` §2.2.
- **Testing**: fake the database boundary (`monkeypatch` on
  `subprocess.run`) so the suite needs no credentials and no network.
- **Timestamps**: generate them with `SYSTIMESTAMP` in SQL, not in Python.
  The pipelines' ledgers are queried against the database clock, and this
  host's database runs at UTC+2; writing Python UTC put two clocks in one
  comparison.
- **Notifications**: follow `../epmc_pipeline/src/notifications.py` — two
  reports, two recipient lists, and never raises into the caller.

## This work is destined for a scientific publication

Record *why*, not only *what*: alternatives considered, why rejected, and the
measurement behind the choice — in commit messages and in
`openspec/changes/*/design.md`. Prefer measuring to asserting. Several
findings in the sibling projects became results precisely because they were
measured rather than assumed.
