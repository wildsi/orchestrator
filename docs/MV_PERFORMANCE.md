# Why the dashboard charts are slow, and what to do about it

Analysis, 2026-09-23. **Nothing changed.** Sources: the read-only check of
the same day (approved): database version and parameters, the SQL of
every APEX region reading an MV, and column statistics of the five
largest MVs; then (second and third checks, approved) the INMEMORY
attributes, `V$IM_SEGMENTS`, and the SQL of all 55 chart series of app
1000. Plus `../epmc_pipeline/db_inventory/` (definitions, sizes, indexes).

## 1. What the database is

- **Oracle AI Database 26ai Enterprise Edition, 23.26.2** (`compatible`
  23.6.0). Not 19c, which earlier notes assumed; the 19c facts they cite
  still hold here.
- **`inmemory_size` = 200 GB.** An In-Memory column store is already
  allocated; `inmemory_force` is DEFAULT, so only objects marked
  `INMEMORY` use it. **18 objects already are** (checked 2026-09-23),
  among them `MV_01_DSI_ALL_PUBLICATIONS`, the other big `MV_00_*` /
  `MV_01_*`, `PMC_REFERENCES`, `ENA_SEQUENCES` and the `N_*` tables - all
  priority LOW (NONE for three), compression FOR QUERY LOW/HIGH. So
  In-Memory is in use and presumably licensed; the DBA should still
  confirm. Not marked: `MV_01_PROVIDING_TO_Y_COUNTRIES`,
  `MV_01_USING_FROM_X_COUNTRIES` (page 10) and all `EPMC_*` tables.
- Partitioning is also a separately licensed EE option. Same question.

## 2. What the dashboard reads

App **1000** (WiLDSi); app 102 is an MV admin page over `MV_REFRESH_LOG`
(it shows the orchestrator's rows, including run 2's false-positive
ERROR). Regions plus chart series, by MV read:

| MV (rows) | read by |
|---|---|
| `MV_01_DSI_ALL_PUBLICATIONS` (22.9M) | 18 series + the reports and D3 charts of pages 6-10, 17 |
| `MV_01_PROVIDING_TO_Y_COUNTRIES`, `MV_01_USING_FROM_X_COUNTRIES` (5.3M each) | **12 series** + the report of page 10 |
| `MV_00_JOIN_COUNTRY_ENA` (36M) | 8 series, pages 5, 9, 18 |
| `MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY` (107M) | 2 series (pages 5, 18): one pie chart |
| `MV_01_JOIN_ENA_LEFTJOIN` (64.5M) | 2 series (pages 5, 18) |
| `MV_00_JOIN_COUNTRY_PMC`, `MV_00_JOIN_PMC_LEFTJOIN` | 2 series each (pages 5, 18) |
| `MV_COUNTRY_GRP_STATS` (plain table, 11.6k) | page 17 |
| `ZZ_MV_HISTOGRAM_PAT_COUNT_MD5` | page 15 |

**Neither `MV_PMC_WITH_ANNOTATIONS` nor `..._ALL_AUTHORS` is read by any
region or series** - taking them out of the chain (§5) is safe.

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
3. **Page 5 / 18's three pie charts aggregate whole MVs with no filter
   at all** - `MV_00_JOIN_COUNTRY_ENA` (36M), `MV_00_JOIN_COUNTRY_PMC`
   and `MV_01_JOIN_ENA_LEFTJOIN_LIT_COUNTRY` (107M) - and each does it
   twice (the chart rows, then an "Other" row from the same subquery).
   Their result changes only when the MV is refreshed, yet it is
   recomputed on every page view. The 107M-row MV, 6 GB on disk and 3 GB
   in memory, exists for this one pie.
4. **Page 10's report joins two 5.3M-row MVs on `country` alone**
   (`mv_01_using_from_x_countries a JOIN mv_01_providing_to_y_countries b
   ON a.country = b.country`). Per country that is every row of one side
   times every row of the other, before `COUNT(DISTINCT …)` - potentially
   billions of intermediate rows. The filters touch only one side, so the
   join contributes nothing but an existence test. **The same query is
   in all 12 chart series of page 10** (G77 / OECD / BRICS and their
   trendlines, each once per dataset branch), so one page view runs it
   six times. The worst query found.
5. **Memory is not the problem.** `V$IM_SEGMENTS`: the dashboard MVs are
   fully populated (`MV_01_DSI_ALL_PUBLICATIONS` COMPLETED, 0.38 GB in
   memory, 0 not populated). The slowness is query shape, not disk.

## 4. Proposals, ranked by gain per risk

Nothing below is applied. Each DB change needs approval; APEX edits are
made in the App Builder by the app owner.

### P0. Precompute the three unfiltered pie charts (3 small MVs + APEX edit)

One row per country instead of a scan of up to 107M rows per view:

```sql
CREATE MATERIALIZED VIEW mv_02_pie_dsi_origin REFRESH COMPLETE ON DEMAND AS
SELECT simplified_name AS country_of_origin,
       COUNT(DISTINCT accession) AS dsi_contribution, COUNT(*) AS n_rows
FROM   mv_00_join_country_ena GROUP BY simplified_name;

CREATE MATERIALIZED VIEW mv_02_pie_dsi_user REFRESH COMPLETE ON DEMAND AS
SELECT simplified_name,
       COUNT(DISTINCT idpmc) AS all_lit, COUNT(*) AS n_rows
FROM   mv_00_join_country_pmc GROUP BY simplified_name;

CREATE MATERIALIZED VIEW mv_02_pie_dsi_used_in_pub REFRESH COMPLETE ON DEMAND AS
SELECT lit_country,
       COUNT(DISTINCT accession) AS all_dsi, COUNT(*) AS n_rows
FROM   mv_01_join_ena_leftjoin_lit_country GROUP BY lit_country;
```

The chart SQL keeps its shape and reads the summary; `n_rows` carries
the `COUNT(*)` the percentages are built from, so the output is
identical:

```sql
SELECT * FROM (SELECT country_of_origin, dsi_contribution,
                      ROUND(n_rows / SUM(n_rows) OVER (), 4) AS percentages
               FROM   mv_02_pie_dsi_origin)
WHERE  percentages > 0.005
UNION
SELECT 'Other', 0, 1 - SUM(percentages)
FROM  (SELECT ROUND(n_rows / SUM(n_rows) OVER (), 4) AS percentages
       FROM   mv_02_pie_dsi_origin)
WHERE  percentages > 0.005;
```

New names, so nothing breaks; the orchestrator's graph places them one
level above their source automatically.

**Measured on the first one** (`MV_02_PIE_DSI_ORIGIN`, 2026-09-23): filled
by the orchestrator in 7.52 s; output identical to the live pie query
(MINUS both ways, no rows); the pie query itself went from **16.65 s to
0.00 s** (physical reads 1,088,655 -> 0, consistent gets 3,939 -> 29).
Pages 5 and 18 each carry three such pies, each computing its aggregate
twice. Later, the 107M-row MV could be
retired by computing its pie straight from its defining query at
refresh time - saving ~21 min of refresh (1,288 s on 02-02), 6 GB of disk
and 3 GB of column store.

**P0 does not reach the charts users filter.** The user's chart is
"Providing DSI countries based on provider" (series `S_providing_DSI`),
which filters by date, taxonomy, author role, publication type and the
treaty dataset; a per-country pie summary drops all of that
(user, 2026-09-23). `MV_02_PIE_DSI_ORIGIN` serves only the unfiltered
"Distribution of provision and use" region; drop it if that region is not
shown.

### P0b. Filter-preserving summary for sequence counts (measured)

The filtered chart counts `COUNT(DISTINCT accession)` per `dsi_country`
over `MV_01_JOIN_ENA_LEFTJOIN` (64.5M rows). An accession's country,
submission date, `CODE` and `TAXID` are fixed; only the publication
type x author role combinations repeat it. So one row per
`(dsi_country, submission_date, code, in_annex1, role_mask)` with a
count of accessions - `role_mask` a 6-bit set of the combinations
(P-F 1, P-R 2, P-S 4, S-F 8, S-R 16, S-S 32; 0 = none) - turns
`COUNT(DISTINCT)` into `SUM`, and every filter still applies:

```sql
SELECT dsi_country AS country_of_origin, SUM(n_accessions) AS dsi_contribution
FROM   mv_02_dsi_provider
WHERE  submission_date BETWEEN TO_DATE(:DP_G_DATE_FROM, 'YYYY-MM-DD') AND TO_DATE(:DP_G_DATE_TO, 'YYYY-MM-DD')
AND    InStr(':' || :CB_TAXONOMY || ':', ':' || code || ':') > 0
AND    (:RB_DATASET = 'All' OR in_annex1 = 1)
AND    (<no publication/author selected> OR BITAND(role_mask, <selected mask>) > 0)
GROUP  BY dsi_country ORDER BY dsi_contribution DESC
```

Exact, because the original keeps an accession if *any* of its rows has
a selected role *and* a selected type - i.e. its mask shares a bit with
the selection.

**Measured 2026-09-23** (read-only, 4 min 00 s): 36,152,968 accessions;
**545,696 summary rows** against 64.5M (~118x fewer); but **2,011
accessions (0.0056%) have more than one country / date / code / taxid**,
so would sit in two groups. Being characterised before anything is built.

### P1. Rewrite page 10's report and its 12 series (APEX edit only, no DDL)

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
switching. The 12 series carry the same two subqueries plus
`WHERE grp = '<group>'` (and trendline variants): the same rewrite
applies to each.

Page 17 already reads a precomputed table, `MV_COUNTRY_GRP_STATS`, and is
fast for it - but two cautions before copying that approach: (a) it is a
**plain table**, not an MV, so nothing refreshes it (last analysed
2026-05-20; who fills it is unknown); (b) it `SUM`s per-day counts,
which over a multi-day range counts a country pair once per day, not
once - not the same number as page 10's `COUNT(DISTINCT …)`.

### P2. In-Memory: only page 10's two MVs are missing

Measured: `MV_01_DSI_ALL_PUBLICATIONS` and the other dashboard MVs are
fully populated, so raising their priority gains nothing. Page 10's two
MVs are not marked at all:

```sql
ALTER MATERIALIZED VIEW mv_01_providing_to_y_countries INMEMORY PRIORITY HIGH;
ALTER MATERIALIZED VIEW mv_01_using_from_x_countries   INMEMORY PRIORITY HIGH;
```

Reversible (`NO INMEMORY`). Worth it only together with P1: marking
them does not fix a cross join.

**For the DBA, not ours to fix:** `V$IM_SEGMENTS` shows `PMC_REFERENCES`
(7.3 GB populated, 0.69 GB missing) and `ENA_SEQUENCES` as **OUT OF
MEMORY** - the shared 200 GB store (1,176 segments across all schemas)
is full. The legacy `PMC_REFERENCES` (19 GB) competing for it will matter
less once the legacy path is retired.

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
- **Draft:** `../epmc_pipeline/src/schema/switch_mvs_to_epmc.sql` rewrites
  `MV_00_JOIN_ENA_PMC` and `MV_00_JOIN_COUNTRY_PMC` to read `EPMC_*`,
  via `DROP … PRESERVE TABLE` + `CREATE … ON PREBUILT TABLE` so the
  containers - and every reader - stay up. Not applied.
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

## 6. Checks done

- 2026-09-23: version, parameters, APEX regions, column statistics.
- 2026-09-23: INMEMORY attributes; chart-series column is `DATA_SOURCE`
  (APEX 24.2 and 26.1 installed).
- 2026-09-23: `V$IM_SEGMENTS` and the 55 chart series of app 1000.
