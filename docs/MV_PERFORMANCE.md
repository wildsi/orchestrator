# Why the dashboard charts are slow, and what to do about it

Analysis, 2026-09-23. **Nothing changed.** Sources: the read-only check of
the same day (approved): database version and parameters, the SQL of
every APEX region reading an MV, and column statistics of the five
largest MVs. Plus `../epmc_pipeline/db_inventory/` (definitions, sizes,
indexes). Chart-*series* SQL is still missing; see §6.

## 1. What the database is

- **Oracle AI Database 26ai Enterprise Edition, 23.26.2** (`compatible`
  23.6.0). Not 19c, which earlier notes assumed; the 19c facts they cite
  still hold here.
- **`inmemory_size` = 200 GB.** An In-Memory column store is already
  allocated; `inmemory_force` is DEFAULT, so only objects marked
  `INMEMORY` use it. Whether any MV is marked is not yet known (§6).
  Database In-Memory beyond the free 16 GB Base Level is a paid option:
  **confirm the licence with the DBA** before relying on it.
- Partitioning is also a separately licensed EE option. Same question.

## 2. What the dashboard reads

App **1000** (WiLDSi). Every chart region found reads
**`MV_01_DSI_ALL_PUBLICATIONS`** (22.9M rows, 2.1 GB); page 10 also
reads `MV_01_PROVIDING_TO_Y_COUNTRIES` / `MV_01_USING_FROM_X_COUNTRIES`
(5.3M rows each). No region reads the 107M-row
`MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY` or the other giants directly - they
matter for refresh time, not page load (unless a chart series reads them,
§6). App 102 is an MV admin page over `MV_REFRESH_LOG`.

Every chart has the same shape:

```sql
SELECT lit_country, COUNT(DISTINCT author_sha256 | publications | dsi_country)
FROM   mv_01_dsi_all_publications
WHERE  submission_date BETWEEN TO_DATE(:DP_G_DATE_FROM, …) AND TO_DATE(:DP_G_DATE_TO, …)
AND    InStr(':' || :CB_TAXONOMY  || ':', ':' || code        || ':') > 0
AND    InStr(':' || :RB_G_AUTHORS || ':', ':' || author_role || ':') > 0
AND    literature_cite_type IN (…)             -- or an InStr on :CB_PUBLICATION
[AND   taxid IN (SELECT taxid FROM treaty_annex1)]   -- the non-"All" dataset
GROUP  BY lit_country [, dsi_country]
```

Column statistics (`MV_01_DSI_ALL_PUBLICATIONS`, analysed 2026-05-27):
`CODE` 15 distinct, `AUTHOR_ROLE` 3, `LITERATURE_CITE_TYPE` 2,
`SUBMISSION_DATE` 6,231, `DSI_COUNTRY` 197, `LIT_COUNTRY` 175,
`PUBLICATIONS` 90,976, `AUTHOR_SHA256` 296,192, **`TAXID` 654,080**.

## 3. Why it is slow

1. **Every chart scans the whole MV, every page load.** The `InStr(':' ||
   :ITEM || ':', …)` filters cannot use an index (the column sits inside
   an expression), and the low-cardinality columns would not make a
   selective index anyway. The date range is the only selective filter,
   and there is no index on `SUBMISSION_DATE`. `COUNT(DISTINCT …)` then
   sorts or hashes what survives. A page with several charts does this
   several times over.
2. **The MV is bigger than the charts need.** `TAXID` (654k distinct
   values) is in the MV only to serve the `treaty_annex1` filter, and it
   multiplies the rows: the MV is DISTINCT over all nine columns, so each
   publication-author-country combination is repeated per taxon. The
   charts never group by taxon. By how much it inflates the MV is not yet
   measured (P3).
3. **Page 10's report joins two 5.3M-row MVs on `country` alone**
   (`mv_01_using_from_x_countries a JOIN mv_01_providing_to_y_countries b
   ON a.country = b.country`). Per country that is every row of one side
   times every row of the other, before `COUNT(DISTINCT …)` - potentially
   billions of intermediate rows. The filters touch only one side, so the
   join contributes nothing but an existence test. **This is the single
   worst query found.**

## 4. Proposals, ranked by gain per risk

Nothing below is applied. Each DB change needs approval; APEX edits are
made in the App Builder by the app owner.

### P1. Rewrite page 10's report (APEX edit only, no DDL)

Same result, no cross join: aggregate each MV on its own, keep the
existence test as a semi-join.

```sql
SELECT x.country, x.count_providing_to_y_countries,
       y.count_using_from_x_countries, cg.grp
FROM  (SELECT b.country, COUNT(DISTINCT b.providing_to_y_countries) count_providing_to_y_countries
       FROM   mv_01_providing_to_y_countries b
       WHERE  <the b filters as today>
       AND    EXISTS (SELECT 1 FROM mv_01_using_from_x_countries a WHERE a.country = b.country)
       GROUP  BY b.country) x
JOIN  (SELECT a.country, COUNT(DISTINCT a.using_from_x_countries) count_using_from_x_countries
       FROM   mv_01_using_from_x_countries a
       WHERE  <the a filters as today>
       AND    EXISTS (SELECT 1 FROM mv_01_providing_to_y_countries b WHERE b.country = a.country)
       GROUP  BY a.country) y ON x.country = y.country
JOIN   country2grp cg ON cg.country = x.country
```

Note the original's `y` subquery groups by `b.country` but counts
`a.using_from_x_countries`; with the join on `a.country = b.country`
those are the same country, so grouping by `a.country` is equivalent.
Verify on one filter setting by comparing both result sets before
switching.

### P2. Put the dashboard MV in memory (one DDL, if licensed)

```sql
ALTER MATERIALIZED VIEW mv_01_dsi_all_publications INMEMORY PRIORITY HIGH;
```

The In-Memory column store is built for exactly this: full scans with
filters and aggregates over a few columns. 2.1 GB on disk, typically
less once columnar-compressed - a small share of the 200 GB allocated. No
query changes, no refresh changes (the column store is repopulated after
each refresh). Reversible with `NO INMEMORY`. **Gate: DBA confirms the
In-Memory licence.**

### P3. A slimmer chart MV without `TAXID` (new MV, then repoint charts)

Replace `TAXID` by a flag, keep only what charts filter, group or count:

```sql
CREATE MATERIALIZED VIEW mv_02_dsi_chart
REFRESH COMPLETE ON DEMAND AS
SELECT DISTINCT
       d.submission_date, d.code, d.author_role, d.literature_cite_type,
       d.dsi_country, d.lit_country, d.publications, d.author_sha256,
       CASE WHEN t.taxid IS NOT NULL THEN 'Y' ELSE 'N' END AS in_annex1
FROM   mv_01_dsi_all_publications d
LEFT   JOIN (SELECT DISTINCT taxid FROM treaty_annex1) t ON t.taxid = d.taxid;
```

Charts then filter `in_annex1 = 'Y'` instead of `taxid IN (…)`. The
row reduction is **not yet measured**; measure it before building (a
`COUNT(*)` of the query above, approval - it scans the 2.1 GB MV once). A new name, so no existing MV or chart breaks; charts
move one at a time. Further splits (one MV for publication counts
without `author_sha256`, one for author counts without `publications`)
shrink it more.

### P4. Index or partition on `SUBMISSION_DATE` (only if P2 is not available)

A B-tree on `SUBMISSION_DATE` helps only narrow date ranges; the default
range likely covers most rows, where a full scan wins anyway.
Interval-partitioning the chart MV by year of `SUBMISSION_DATE` prunes
reliably but needs the Partitioning licence and a rebuild. With P2 in
place neither is worth it.

### P5. Make the filters sargable (APEX edit, small gain alone)

`InStr(':' || :ITEM || ':', ':' || col || ':') > 0` →
`col IN (SELECT column_value FROM apex_string.split(:ITEM, ':'))`. On
15 / 3 / 2-value columns this alone changes little; it matters once a
composite index or partition exists. Low priority.

## 5. The switch-over (epmc task 4.2) is itself an improvement

Only three MVs read `N_*`: `MV_PMC_WITH_ANNOTATIONS`,
`MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS`, `MV_00_JOIN_ENA_PMC`.

- `MV_PMC_WITH_ANNOTATIONS_ALL_AUTHORS` (17M rows, 6 GB) is DISTINCT
  over publication x annotation x author, but its only MV reader,
  `MV_00_JOIN_COUNTRY_PMC` (1.6M rows), needs one row per author per
  annotated publication. Reading `EPMC_*` directly there, with
  `EXISTS (… EPMC_ANNOTATION …)` for "has an annotation", removes a 6 GB
  intermediate that has not refreshed since its November 2025 import
  (ORA-00942 on 02-02, `MV_STATE.md` §2.1).
- The switch should read the `EPMC_*` tables, **not**
  `EPMC_V_PMC_REFERENCES_FLAT`, which rebuilds the old wide join.
- Numbers will change: the rebuild holds +298,878 publications the
  legacy pull lost (`../epmc_pipeline/docs/FINDINGS.md` §D).

**A defect to decide on, not silently fix:** `MV_00_JOIN_COUNTRY_PMC`
builds `author_sha256` from `substr(a.affiliation, 3500)` - the text
*from position 3500 on*, which is empty for almost every affiliation
(the longest observed in 5.7M sampled rows, 2022-2026, is 2,833 chars). So the author identity is
effectively `last_name,first_name`, and "total authors" merges namesakes.
`SUBSTR(affiliation, 1, 3500)` was presumably meant; fixing it changes
every author count on the dashboard. The switch-over should reproduce the
current hash exactly, and the fix be a separate, announced change.

## 6. Still to read (read-only, needs approval)

```sql
-- the real column name of the chart-series source (SERIES_SOURCE does not exist)
SELECT owner, column_name FROM all_tab_columns
WHERE  table_name = 'APEX_APPLICATION_PAGE_CHART_S'
ORDER  BY owner, column_id;
-- which tables / MVs are already INMEMORY
SELECT table_name, inmemory, inmemory_priority, inmemory_compression
FROM   user_tables WHERE inmemory = 'ENABLED';
```
