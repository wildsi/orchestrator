# Materialized view state and refresh order

Written 2026-09-23 from `../epmc_pipeline`, where the 15 INVALID MVs were
recompiled (epmc-new-tables task 4.1). Filed here because the refresh is
this project's job. **Nothing has been refreshed.** Sources: the read-only
inventory in `../epmc_pipeline/db_inventory/` (re-captured 2026-09-23) and
the per-MV compile log in
`../epmc_pipeline/openspec/changes/epmc-new-tables/tasks.md`.

## 1. The 15 INVALID MVs are recompiled (task 3.1 here)

One `ALTER MATERIALIZED VIEW … COMPILE` per MV, each approved and checked
before the next, via `../epmc_pipeline/src/schema/recompile_invalid_mvs.sql`.
All 15 compiled: **no definition was broken** by whatever marked them
NEEDS_COMPILE on 2026-02-04..08. No data changed - every MV kept its
`LAST_REFRESH_DATE`. The only invalid object left is the APEX trigger
`APEX$TEAM_DEV_FILES_BIU`, which is not ours.

## 2. What the compile revealed: 10 of 25 MVs are UNUSABLE

While an MV is NEEDS_COMPILE, Oracle reports that instead of its real
staleness. Once compiled, it showed:

| staleness | MVs |
|---|---|
| FRESH (4) | MV_00_JOIN_PMC_LEFTJOIN, MV_01_DSI_ALL_PUBLICATIONS, MV_01_USING_FROM_X_COUNTRIES, ZZ_MV_HISTOGRAM_PAT_COUNT_MD5 |
| STALE (10) | MV_NUM_PUB, MV_NUM_AUTHORS, MV_NUM_AUTHORS_COUNTRY, MV_ACC_PRI_LIT, MV_ACC_PRI_OR_SEC_LIT_01, MV_ACC_SEC_LIT, MV_ACC_PRI_OR_SEC_LIT, MV_ACC_IN_PRIMARY_AND_SECONDARY_LIT, MV_01_JOIN_ENA_LEFTJOIN, MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY |
| **UNUSABLE (10)** | MV_ACC_SEC_LIT_01, MV_ACC_SEC_LIT_02, MV_ACC_PRI_OR_SEC_LIT_02, MV_ACC_PRI_OR_SEC_LIT_03, MV_00_JOIN_COUNTRY_ENA, MV_00_JOIN_COUNTRY_PMC, MV_00_JOIN_ENA_PMC, MV_01_PROVIDING_TO_Y_COUNTRIES, MV_PMC_WITH_ANNOTATIONS, MV_01_IN_COUNTRY_USE |
| IMPORT (1) | MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS (came in by import; last refresh 2025-11-19) |

Confirmed by a single inventory snapshot taken after all 15 compiles
(2026-09-23, approved): every MV's staleness is as above.

Oracle 19c reference, `ALL_MVIEWS.STALENESS`: STALE is "masters have
changed" since a consistent refresh; **UNUSABLE is "not a read-consistent
view of its masters at any point in time"**. A compile cannot clear it;
only a successful refresh can.

**Cause: not established.** One hypothesis ("MVs reading both
PMC_REFERENCES and ENA_SEQUENCES") was refuted by MV_00_JOIN_COUNTRY_ENA.
Staleness is judged per MV against its direct masters only: MV_ACC_SEC_LIT
is STALE although both its parents are UNUSABLE.

**Strongest lead: how the February refresh was run.** The only refresh
code found under `Scripts/` is `../ena_pipeline/legacy_variant/run_all.sh`:

```sql
DBMS_MVIEW.REFRESH_ALL_MVIEWS(failures, 'C', '', TRUE, FALSE, FALSE);
--                                  method  rbs  refresh_after_errors
--                                                     atomic_refresh = FALSE
```

Per the 19c `DBMS_MVIEW` reference, `atomic_refresh => FALSE` refreshes
each MV in its own transaction and a COMPLETE refresh then starts with a
TRUNCATE; with `refresh_after_errors => TRUE` it carries on to the next MV
after a failure. That combination can leave an MV truncated or partly
filled, and parents and children refreshed at unrelated points in time.
Whether *this* script is what ran in February is **unverified** - the
brief says the refresh was manual - and Oracle's own refresh history
(`USER_MVREF_STATS`) keeps 31 days by default, so February is gone there.

This is the practical risk: an UNUSABLE MV may be **empty or partial right
now**, i.e. APEX may be showing wrong numbers, not merely old ones. The
optimizer row counts cannot rule it out (they were gathered in February).

Read-only checks that would settle it, **each needs approval**:
1. `SELECT * FROM MV_REFRESH_LOG ORDER BY 1` - a 28-row custom table, last
   analysed 2026-02-02; may record who refreshed what, when, and failures.
2. Per UNUSABLE MV, an emptiness probe that reads at most one row:
   `SELECT COUNT(*) FROM <mv> WHERE ROWNUM <= 1`.

Decision 2 in `CLAUDE.md` (`atomic_refresh=TRUE`) already avoids the
truncate-first behaviour; this finding is evidence for it.

### 2.1 What `MV_REFRESH_LOG` records (read 2026-09-23, approved)

Columns `LOG_ID, LOG_TIME, MV_NAME, STATUS, ERROR_MESSAGE, DURATION_SEC`.
28 rows, two runs: 2026-01-30 (IDs 1-3, the three `MV_00_JOIN_*` only) and
2026-02-02 (IDs 21-45, all 25 MVs). IDs 4-20 are missing, most likely a
lost identity cache rather than deleted rows. The writer is the stored
procedure `REFRESH_ALL_MVS`. Both are in `REFRESH_LOG.md` §1.

**1. `MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS` failed**, in 0.02 s:
`ORA-00942: table or view does not exist`. Its staleness is still IMPORT
and its last refresh 2025-11-19, i.e. it has **never refreshed since it
was imported**. `MV_00_JOIN_COUNTRY_PMC` reads it, and all eight level-2
MVs read that, so the whole author/country side of the application rests
on November 2025 data. All three masters (`N_PMC_REFERENCES`,
`N_ANNOTATIONS`, `N_AUTHOR`) exist today; which one was missing on
02-02 is not recorded.

**2. The run went children before parents.** Against the order in §3:

| child, log ID | refreshed before its parent(s), log ID |
|---|---|
| MV_00_JOIN_COUNTRY_PMC, 22 | MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS, 44 (failed) |
| MV_00_JOIN_ENA_PMC, 23 | MV_PMC_WITH_ANNOTATIONS, 43 |
| MV_ACC_SEC_LIT, 32 | MV_ACC_SEC_LIT_01, 37; _02, 38 |
| MV_ACC_PRI_OR_SEC_LIT, 33 | MV_ACC_PRI_OR_SEC_LIT_01..03, 34-36 |

Each of these children was therefore built from its parent's *previous*
contents. Every entry says OK, so the log shows no failure behind the
UNUSABLE state - only this ordering, which is a plausible route to it,
not a proven one.

**3. Timing (for task 1.4).** The 02-02 run took **13,640 s = 3.8 h**,
sequential. Largest: MV_00_JOIN_PMC_LEFTJOIN 3,632 s, MV_01_USING_FROM_X_
COUNTRIES 3,218 s, MV_01_PROVIDING_TO_Y_COUNTRIES 1,390 s,
MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY 1,288 s. These are non-atomic figures
(to be confirmed: the log does not record the refresh options); an atomic
refresh deletes instead of truncating and will be slower.
`LAST_REFRESH_DATE` matches the log's run start plus the cumulative
durations (MV_00_JOIN_* at 06:41-06:43, MV_00_JOIN_PMC_LEFTJOIN done by
07:45).

**4. The log does not cover the later events.** `USER_MVIEWS` shows 12
MVs last refreshed 2026-02-04 15:09 (all in the same minute despite
multi-minute durations), MV_01_PROVIDING_TO_Y_COUNTRIES at 02-04 15:02,
MV_01_USING_FROM_X_COUNTRIES at 02-10 08:35, and the 15 MVs went
NEEDS_COMPILE on 02-08 13:48-13:51. None of that is in this table, so at
least one other mechanism refreshed (or altered) MVs after 02-02. No MV
carries a schedule that would explain it (§5), so a manual refresh is the
simplest explanation left; the 02-08 invalidation is unexplained too.
Check 2 above (emptiness probe) is still needed.

## 3. Refresh order (answers task 1.3)

Topological levels from `user_dependencies`
(`../epmc_pipeline/db_inventory/03_mv_dependencies.txt`), 25 MVs. An MV may
refresh only after every MV it reads:

| level | MVs | reads MVs |
|---|---|---|
| 0 (13) | MV_PMC_WITH_ANNOTATIONS, MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS, MV_00_JOIN_COUNTRY_ENA, MV_ACC_PRI_LIT, MV_ACC_SEC_LIT_01, MV_ACC_SEC_LIT_02, MV_ACC_PRI_OR_SEC_LIT_01/_02/_03, MV_NUM_PUB, MV_NUM_AUTHORS, MV_NUM_AUTHORS_COUNTRY, ZZ_MV_HISTOGRAM_PAT_COUNT_MD5 | tables only |
| 1 (4) | MV_00_JOIN_COUNTRY_PMC | MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS |
| | MV_00_JOIN_ENA_PMC | MV_PMC_WITH_ANNOTATIONS |
| | MV_ACC_SEC_LIT | MV_ACC_SEC_LIT_01, _02 |
| | MV_ACC_PRI_OR_SEC_LIT | MV_ACC_PRI_OR_SEC_LIT_01, _02, _03 |
| 2 (8) | MV_00_JOIN_PMC_LEFTJOIN, MV_01_DSI_ALL_PUBLICATIONS, MV_01_IN_COUNTRY_USE, MV_01_JOIN_ENA_LEFTJOIN, MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY, MV_01_PROVIDING_TO_Y_COUNTRIES, MV_01_USING_FROM_X_COUNTRIES | the three MV_00_JOIN_* |
| | MV_ACC_IN_PRIMARY_AND_SECONDARY_LIT | MV_ACC_PRI_LIT, MV_ACC_SEC_LIT |

**Where the names disagree with the graph** (as the brief suspected):
- `MV_00_JOIN_COUNTRY_PMC` and `MV_00_JOIN_ENA_PMC` are "00" but level 1:
  they read `MV_PMC_WITH_ANNOTATIONS*`, which must refresh first.
- `MV_00_JOIN_PMC_LEFTJOIN` is "00" but level 2: it reads all three
  `MV_00_JOIN_*`.
- `MV_02_U_P` and `MV_COUNTRY_GRP_STATS` are **plain tables**, not MVs
  (in `USER_TABLES`, absent from `USER_MVIEWS`); nothing refreshes them.

No cycle. `mv_graph.py` (task 2.2) should reproduce this table from the
fixture; it is a ready-made expected value for its test.

## 4. The unused MV log is 1 GB

`MLOG$_ENA_SEQUENCES`: 1,088 MB (`01_tables.txt`), ROWID-based with new
values. Every MV is `REFRESH COMPLETE ON DEMAND`, so no refresh ever
consumes or purges it; it grows with every ENA load. Relevant to task 1.2:
either a fast refresh starts using it, or it is dead weight. Not touched.

## 5. A weekly scheduler job, not self-refreshing MVs

The starting point (user, 2026-09-23) was that some MVs had been created
with `REFRESH … NEXT SYSDATE + 7`. Such an MV gets an implicit refresh
group and refreshes itself on that clock, while `REFRESH_MODE` still reads
DEMAND.

**Measured 2026-09-23** (`../epmc_pipeline/tools/inspect_db.sh` section 12,
approved):
- `USER_REFRESH_CHILDREN` is **empty**. No MV in this schema carries a
  `NEXT` schedule today.
- `USER_SCHEDULER_JOBS` holds one job, `REFRESH_MV_COUNTRY_ENA`: enabled,
  repeat interval `SYSDATE + 7`, **42 runs, 0 failures**, last start
  2026-09-20 11:00, next run 2026-09-27 11:00.
- Yet `MV_00_JOIN_COUNTRY_ENA`, the MV its name points to, was last
  refreshed 2026-02-02. The next check explains why.

**What the job runs (checked 2026-09-23, approved): nothing.** It is a
`PLSQL_BLOCK` job with an empty `JOB_ACTION`, no program and no named
schedule; no scheduler programs exist in the schema. Its last run took
**0.0116 s**, and the four runs still in the history (2026-08-30 to
09-20) all SUCCEEDED in 0 s. The smallest refresh on record took over a
second (§2.1), and `MV_00_JOIN_COUNTRY_ENA` itself 57 s. The job is an
empty shell that still fires weekly - most likely its body was cleared
while the schedule was left in place.

Consequences:
- **It does not race the orchestrator.** Retiring it is housekeeping
  (`DBMS_SCHEDULER.DROP_JOB`, needs approval), not a precondition.
- The refreshes on 02-04 and 02-10 that are missing from `MV_REFRESH_LOG`
  (§2.1, point 4) are **not explained** by it. No MV carries a schedule
  either, so a manual refresh is the simplest explanation left.
