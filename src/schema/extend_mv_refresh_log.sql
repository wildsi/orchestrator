-- ---------------------------------------------------------------------------
-- Extend MV_REFRESH_LOG for the orchestrator's refresh stage.
--
-- NOT APPLIED. Needs explicit approval, one statement at a time.
-- Design and rationale: docs/REFRESH_LOG.md. src/refresh.py writes these
-- columns; until this is applied, `run_refresh.py --execute` fails at its
-- first INSERT (ORA-00904), before any refresh.
--
-- Safety:
--   * Nullable columns, no DEFAULT: a dictionary-only change. No row is
--     rewritten; the table holds 28 rows.
--   * The only other writer, the stored procedure REFRESH_ALL_MVS, inserts
--     with a column list (checked 2026-09-23), so it keeps working.
--   * No materialized view reads this table (not in
--     ../epmc_pipeline/db_inventory/03_mv_dependencies.txt), so nothing the
--     APEX application shows is affected.
--   * Reversible: ALTER TABLE MV_REFRESH_LOG DROP (<column>, ...).
-- ---------------------------------------------------------------------------

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

-- Verification (expected: 22 columns; the index VALID).
SELECT COUNT(*) AS columns FROM user_tab_columns WHERE table_name = 'MV_REFRESH_LOG';
SELECT index_name, status FROM user_indexes WHERE table_name = 'MV_REFRESH_LOG';
