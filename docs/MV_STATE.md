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

The 15 recompiled MVs show their state right after their own compile; the
other 10 show the inventory taken just before the compiles. Recompiling a
parent may have shifted a child's reported staleness since - re-run
`../epmc_pipeline/tools/inspect_db.sh` (approval) for a single snapshot.

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
