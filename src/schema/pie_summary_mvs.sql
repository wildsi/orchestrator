-- ---------------------------------------------------------------------------
-- P0: precompute the three unfiltered pie charts of app 1000, pages 5 / 18.
-- Rationale and the replacement chart SQL: docs/MV_PERFORMANCE.md, P0.
--
-- Each is created BUILD DEFERRED (a definition only, reads no data) and
-- filled by the orchestrator, so the first fill is logged and checked:
--     uv run python src/run_refresh.py --execute --only <MV>
-- One statement per approval. New names: nothing reads them until the pie's
-- SQL is switched in APEX, so creating them changes nothing users see.
--
-- Applied:
--   2026-09-23 MV_02_PIE_DSI_ORIGIN created (deferred); filled by run 4:
--              level 1, OK, has rows 0 -> 1, FRESH / VALID, 7.52 s.
-- ---------------------------------------------------------------------------

-- "Providers of all DSI"
CREATE MATERIALIZED VIEW MV_02_PIE_DSI_ORIGIN
BUILD DEFERRED
REFRESH COMPLETE ON DEMAND
AS
SELECT simplified_name           AS country_of_origin,
       COUNT(DISTINCT accession) AS dsi_contribution,
       COUNT(*)                  AS n_rows
FROM   mv_00_join_country_ena
GROUP  BY simplified_name;

-- "DSI user locations" - NOT APPLIED
CREATE MATERIALIZED VIEW MV_02_PIE_DSI_USER
BUILD DEFERRED
REFRESH COMPLETE ON DEMAND
AS
SELECT simplified_name,
       COUNT(DISTINCT idpmc) AS all_lit,
       COUNT(*)              AS n_rows
FROM   mv_00_join_country_pmc
GROUP  BY simplified_name;

-- "Provider of DSI used in a publication" - NOT APPLIED
CREATE MATERIALIZED VIEW MV_02_PIE_DSI_USED_IN_PUB
BUILD DEFERRED
REFRESH COMPLETE ON DEMAND
AS
SELECT lit_country,
       COUNT(DISTINCT accession) AS all_dsi,
       COUNT(*)                  AS n_rows
FROM   mv_01_join_ena_leftjoin_lit_country
GROUP  BY lit_country;
