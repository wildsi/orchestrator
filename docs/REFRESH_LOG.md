# Refresh log: reuse `MV_REFRESH_LOG`, extended

Proposal, 2026-09-23. **Nothing applied.** The DDL below needs approval,
and one read-only check comes first (§1).

## Why reuse it

`MV_REFRESH_LOG` already holds the only surviving record of the February
refreshes (`MV_STATE.md` §2.1). Oracle's own `USER_MVREF_STATS` is purged
after 31 days, so a durable log in our schema is necessary, and keeping one
table keeps one history. Today it has
`LOG_ID, LOG_TIME, MV_NAME, STATUS, ERROR_MESSAGE, DURATION_SEC`.

What it could not tell us in September: which run a row belongs to, the
refresh options, whether an MV ended up empty, what state it was left in,
why IDs 4-20 are missing, and what refreshed the MVs on 02-04 and 02-10.
Every column below closes one of those gaps.

## 1. Before any DDL: find the current writer (read-only, needs approval)

Something inside the database writes this table; nothing under `Scripts/`
does. If that code does `INSERT INTO mv_refresh_log VALUES (...)` without a
column list, adding a column breaks it (ORA-00947, not enough values).
Check first:

```sql
SELECT name, type, line, text FROM user_source
WHERE  UPPER(text) LIKE '%MV_REFRESH_LOG%' ORDER BY name, line;
SELECT trigger_name, table_name, status FROM user_triggers
WHERE  table_name = 'MV_REFRESH_LOG';
SELECT sequence_name, last_number FROM user_sequences;   -- how LOG_ID is made
SELECT job_name, enabled, state, last_start_date FROM user_scheduler_jobs;
```

The scheduler query also answers whether a job is still refreshing MVs on
its own - the orchestrator must not race one.

## 2. Added columns (all nullable, so existing rows and writers are unaffected)

| column | type | closes the gap |
|---|---|---|
| `RUN_ID` | NUMBER | groups one orchestrated run; a run is its rows, not a date |
| `SLURM_JOB_ID` | VARCHAR2(32) | links to the job log on disk |
| `ORDER_NO`, `LEVEL_NO` | NUMBER | position in the topological order (`MV_STATE.md` §3), so an out-of-order run is visible in the log itself |
| `START_TIME`, `END_TIME` | TIMESTAMP | exact, instead of a DATE plus a duration |
| `REFRESH_METHOD` | VARCHAR2(1) | `C` / `F` / `?` |
| `ATOMIC_REFRESH` | VARCHAR2(1) | `Y` / `N` - the option most likely behind the UNUSABLE MVs, never recorded |
| `ROWS_BEFORE`, `ROWS_AFTER` | NUMBER | from `USER_MVREF_STATS.INITIAL_NUM_ROWS / FINAL_NUM_ROWS`: free, no `COUNT(*)` on 100M rows |
| `STALENESS_AFTER`, `COMPILE_STATE_AFTER` | VARCHAR2(19) | from `USER_MVIEWS` right after the refresh; anything but FRESH / VALID is a failure even when the refresh "succeeded" |
| `ERROR_CODE` | NUMBER | the ORA number, queryable; `ERROR_MESSAGE` keeps the text |
| `ORACLE_REFRESH_ID` | NUMBER | joins `USER_MVREF_STATS` / `USER_MVREF_RUN_STATS` while Oracle still keeps them |
| `UPSTREAM_ENA_RUN`, `UPSTREAM_EPMC_RUN` | NUMBER | the `ENA_PIPELINE_EXECUTIONS` / `EPMC_PIPELINE_EXECUTIONS` rows whose data this refresh published |

`STATUS` gains two values alongside `OK` / `ERROR`:
- **`STARTED`**, written *before* the refresh and updated after. A job
  killed mid-refresh (Slurm wall clock, node loss) then leaves a row saying
  so, instead of a silent gap like IDs 4-20.
- **`SKIPPED`**, for an MV not refreshed because a parent failed (decision 1,
  halt on failure). Today a failed parent is followed by children refreshed
  from stale input, as on 02-02 (`MV_STATE.md` §2.1, point 2).

```sql
-- PROPOSED, NOT APPLIED. Per-statement approval; run §1 first.
ALTER TABLE MV_REFRESH_LOG ADD (
    RUN_ID              NUMBER,
    SLURM_JOB_ID        VARCHAR2(32),
    ORDER_NO            NUMBER,
    LEVEL_NO            NUMBER,
    START_TIME          TIMESTAMP,
    END_TIME            TIMESTAMP,
    REFRESH_METHOD      VARCHAR2(1),
    ATOMIC_REFRESH      VARCHAR2(1),
    ROWS_BEFORE         NUMBER,
    ROWS_AFTER          NUMBER,
    STALENESS_AFTER     VARCHAR2(19),
    COMPILE_STATE_AFTER VARCHAR2(19),
    ERROR_CODE          NUMBER,
    ORACLE_REFRESH_ID   NUMBER,
    UPSTREAM_ENA_RUN    NUMBER,
    UPSTREAM_EPMC_RUN   NUMBER
);
CREATE INDEX MV_REFRESH_LOG_RUN_IX ON MV_REFRESH_LOG (RUN_ID, ORDER_NO);
```

Adding nullable columns without a default is a dictionary-only change: no
rows rewritten, instant on 28 rows. No MV reads this table (it is absent
from `03_mv_dependencies.txt`), so nothing in APEX is affected.

## 3. Checks the orchestrator derives from each row

- `ROWS_AFTER = 0`, or `ROWS_AFTER < 0.5 * ROWS_BEFORE` -> alert, and halt
  the MVs above it. This is the emptiness question from `MV_STATE.md` §2,
  answered on every run at no cost.
- `STALENESS_AFTER <> 'FRESH'` or `COMPILE_STATE_AFTER <> 'VALID'` -> alert.
- a `STARTED` row with no `END_TIME` from an earlier run -> report it at the
  start of the next run.
- the run summary (one line per MV: order, status, rows before -> after,
  duration) goes into the notification e-mail.

## 4. Related, separate decision

Raising Oracle's own retention (31 days) would keep `USER_MVREF_STATS` long
enough to investigate the next incident:
`DBMS_MVIEW_STATS.SET_MVREF_STATS_PARAMS(NULL, 'TYPICAL', 400)`. That is a
database setting change; needs approval, and possibly the DBA.
