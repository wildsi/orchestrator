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

## The APEX application is the constraint

~25 materialized views back the charts, tables and plots of an Oracle APEX
web app. They exist because the underlying tables hold tens of millions of
rows. Anything that leaves an MV empty, invalid, or inconsistent with its
siblings is visible to users immediately.

Before proposing any operation against an MV, read
`../epmc_pipeline/db_inventory/03_mv_dependencies.txt` and
`04_table_to_dependents.txt`.

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

- **Dump scope.** Deferred deliberately. Check first whether an existing RMAN
  or expdp schedule already covers this — the stage may be redundant. If not,
  candidates are: the pipeline tables only, the full `wildsi` schema, or
  tables plus DDL without MV contents.
- **MV refresh order.** The `MV_00_*` → `MV_01_*` → `MV_02_*` naming implies a
  layered chain that must refresh bottom-up. **Derive it from
  `user_dependencies`, not from the names.**
- **The 15 INVALID MVs.** They are broken independently of this work and
  predate it. Recompiling them is its own step and should happen before the
  first orchestrated refresh, not inside it.
- **Fast refresh.** All MVs are `DEMAND` + `COMPLETE`, yet an
  `MLOG$_ENA_SEQUENCES` exists and is unused. Worth investigating: a fast
  refresh would change the cost of this project entirely.

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

## This work is destined for a scientific publication

Record *why*, not only *what*: alternatives considered, why rejected, and the
measurement behind the choice — in commit messages and in
`openspec/changes/*/design.md`. Prefer measuring to asserting. Several
findings in the sibling projects became results precisely because they were
measured rather than assumed.
