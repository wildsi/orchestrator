"""Refresh the materialized views one at a time, in dependency order.

Decisions (CLAUDE.md, docs/MV_STATE.md, docs/REFRESH_LOG.md):
- One DBMS_MVIEW.REFRESH per MV, COMPLETE, atomic_refresh=TRUE, so every MV
  stays readable throughout; FALSE truncates first.
- Parents before children (mv_graph.refresh_order).
- Halt at the first failure. Every MV not yet refreshed is logged SKIPPED,
  so no child is rebuilt from a parent that failed - the 02-02 run did
  exactly that. What was refreshed before the failure stays refreshed;
  each of those MVs is consistent with its own inputs.
- A refresh that "succeeds" can still fail a check: an MV left empty, one
  that lost more than half its rows, or one not FRESH / VALID afterwards.
- Every MV gets a row in MV_REFRESH_LOG, written STARTED *before* the
  refresh, so a job killed mid-refresh leaves a trace instead of a gap.

The log columns beyond the original six come from
src/schema/extend_mv_refresh_log.sql (applied 2026-09-23). LOG_ID is an
identity column GENERATED ALWAYS: never supplied, read back via RETURNING.
"""

import re
from dataclasses import dataclass

from db import run_sqlplus

# Dictionary names only - they are interpolated into SQL text.
_NAME = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")

RESULT_SENTINEL = "ORCHRESULT"
RUN_ID_SENTINEL = "ORCHRUNID"
UNFINISHED_SENTINEL = "ORCHUNFINISHED"
SKIPPED_SENTINEL = "ORCHSKIPPED"
CHECKED_SENTINEL = "ORCHCHECKED"

# ROWS_AFTER below this fraction of ROWS_BEFORE fails the refresh.
MIN_ROW_RATIO = 0.5


@dataclass
class Step:
    order_no: int
    level_no: int
    mv_name: str


@dataclass
class Outcome:
    mv_name: str
    status: str  # OK, ERROR, SKIPPED
    log_id: int = None
    rows_before: int = None
    rows_after: int = None
    staleness: str = None
    compile_state: str = None
    error_code: int = None
    message: str = None
    duration_sec: float = None


def _name(value):
    if not _NAME.match(value or ""):
        raise ValueError(f"not a plain Oracle object name: {value!r}")
    return value


def _literal(value):
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def _run_id(run_id):
    """A dry-run plan has no RUN_ID yet; it shows a placeholder instead."""
    return "<RUN_ID>" if run_id is None else str(int(run_id))


def steps_from_order(order):
    return [Step(order_no, level_no, _name(mv)) for order_no, level_no, mv in order]


NEXT_RUN_ID_SQL = (
    f"SELECT '{RUN_ID_SENTINEL}|' || (NVL(MAX(run_id), 0) + 1) FROM mv_refresh_log;"
)

UNFINISHED_SQL = f"""
SELECT '{UNFINISHED_SENTINEL}|' || run_id || '|' || mv_name || '|'
       || TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS')
FROM   mv_refresh_log
WHERE  status = 'STARTED' AND end_time IS NULL;
"""


def refresh_block(step, run_id, slurm_job_id=None):
    """The PL/SQL for one MV: log STARTED, refresh, measure, log outcome.

    The refresh's own error is caught so the row is always completed; the
    block then prints one sentinel line. A block that fails to compile or
    dies prints none, and the caller treats that as failure.
    """
    mv = _name(step.mv_name)
    return f"""
DECLARE
    v_log_id    NUMBER;
    v_start     TIMESTAMP := SYSTIMESTAMP;
    v_elapsed   INTERVAL DAY(3) TO SECOND(6);
    v_secs      NUMBER;
    v_prev_ref  NUMBER;
    v_ref_id    NUMBER;
    v_before    NUMBER;
    v_after     NUMBER;
    v_stale     VARCHAR2(19);
    v_compile   VARCHAR2(19);
    v_code      NUMBER;
    v_msg       VARCHAR2(4000);
BEGIN
    INSERT INTO mv_refresh_log (
        log_time, mv_name, status, run_id, slurm_job_id, order_no, level_no,
        start_time, refresh_method, atomic_refresh
    ) VALUES (
        SYSDATE, '{mv}', 'STARTED', {_run_id(run_id)}, {_literal(slurm_job_id)},
        {int(step.order_no)}, {int(step.level_no)}, v_start, 'C', 'Y'
    ) RETURNING log_id INTO v_log_id;
    COMMIT;

    SELECT NVL(MAX(refresh_id), 0) INTO v_prev_ref
    FROM   user_mvref_stats WHERE mv_name = '{mv}';

    BEGIN
        DBMS_MVIEW.REFRESH(list => '{mv}', method => 'C', atomic_refresh => TRUE);
    EXCEPTION WHEN OTHERS THEN
        v_code := SQLCODE;
        v_msg  := SUBSTR(REPLACE(REPLACE(SQLERRM, CHR(10), ' '), '|', '/'), 1, 4000);
    END;

    v_elapsed := SYSTIMESTAMP - v_start;
    v_secs := EXTRACT(DAY FROM v_elapsed) * 86400 + EXTRACT(HOUR FROM v_elapsed) * 3600
            + EXTRACT(MINUTE FROM v_elapsed) * 60 + EXTRACT(SECOND FROM v_elapsed);

    -- Row counts from Oracle's own refresh statistics: free, no COUNT(*).
    -- Absent when statistics collection is NONE; the caller then skips the
    -- row checks and says so.
    BEGIN
        SELECT refresh_id, initial_num_rows, final_num_rows
        INTO   v_ref_id, v_before, v_after
        FROM  (SELECT refresh_id, initial_num_rows, final_num_rows
               FROM   user_mvref_stats
               WHERE  mv_name = '{mv}' AND refresh_id > v_prev_ref
               ORDER  BY refresh_id DESC)
        WHERE ROWNUM = 1;
    EXCEPTION WHEN NO_DATA_FOUND THEN NULL;
    END;

    SELECT staleness, compile_state INTO v_stale, v_compile
    FROM   user_mviews WHERE mview_name = '{mv}';

    UPDATE mv_refresh_log
    SET    status = CASE WHEN v_code IS NULL THEN 'OK' ELSE 'ERROR' END,
           end_time = SYSTIMESTAMP, duration_sec = ROUND(v_secs, 2),
           error_code = v_code, error_message = v_msg,
           rows_before = v_before, rows_after = v_after,
           staleness_after = v_stale, compile_state_after = v_compile,
           oracle_refresh_id = v_ref_id
    WHERE  log_id = v_log_id;
    COMMIT;

    DBMS_OUTPUT.PUT_LINE('{RESULT_SENTINEL}|' || v_log_id || '|'
        || CASE WHEN v_code IS NULL THEN 'OK' ELSE 'ERROR' END || '|'
        || v_before || '|' || v_after || '|' || v_stale || '|' || v_compile || '|'
        || v_code || '|' || ROUND(v_secs, 2) || '|' || v_msg);
END;
/
"""


def check_failed_sql(log_id, problem):
    reason = "CHECK: " + problem
    return f"""
UPDATE mv_refresh_log
SET    status = 'ERROR', error_message = SUBSTR({_literal(reason)}, 1, 4000)
WHERE  log_id = {int(log_id)};
COMMIT;
SELECT '{CHECKED_SENTINEL}|' || {int(log_id)} FROM dual;
"""


def skip_sql(steps, run_id, reason, slurm_job_id=None):
    inserts = "\n".join(
        f"""INSERT INTO mv_refresh_log (
    log_time, mv_name, status, run_id, slurm_job_id, order_no, level_no,
    refresh_method, atomic_refresh, error_message
) VALUES (
    SYSDATE, '{_name(s.mv_name)}', 'SKIPPED', {int(run_id)}, {_literal(slurm_job_id)},
    {int(s.order_no)}, {int(s.level_no)}, 'C', 'Y', SUBSTR({_literal(reason)}, 1, 4000)
);"""
        for s in steps
    )
    return f"{inserts}\nCOMMIT;\nSELECT '{SKIPPED_SENTINEL}|{len(steps)}' FROM dual;\n"


def _number(text, kind=int):
    text = text.strip().replace(",", ".")
    return kind(text) if text else None


def parse_result(mv_name, output_lines):
    """Read the block's sentinel line; no sentinel means failure."""
    for line in output_lines:
        if line.startswith(RESULT_SENTINEL + "|"):
            parts = line.split("|", 9)
            parts += [""] * (10 - len(parts))
            _, log_id, status, before, after, stale, compile_state, code, secs, msg = parts
            return Outcome(
                mv_name=mv_name,
                status=status.strip(),
                log_id=_number(log_id),
                rows_before=_number(before),
                rows_after=_number(after),
                staleness=stale.strip() or None,
                compile_state=compile_state.strip() or None,
                error_code=_number(code),
                duration_sec=_number(secs, float),
                message=msg.strip() or None,
            )
    tail = " / ".join(line.strip() for line in output_lines if line.strip())[-500:]
    return Outcome(mv_name=mv_name, status="ERROR", message="no result line: " + tail)


def check(outcome):
    """Return why a refresh that ran must still count as failed, or None."""
    if outcome.status != "OK":
        return None
    if outcome.rows_after is not None:
        if outcome.rows_after == 0:
            return "materialized view is empty after refresh"
        if outcome.rows_before and outcome.rows_after < MIN_ROW_RATIO * outcome.rows_before:
            return (
                f"rows fell from {outcome.rows_before} to {outcome.rows_after} "
                f"(below {MIN_ROW_RATIO:.0%})"
            )
    if outcome.staleness != "FRESH":
        return f"staleness after refresh is {outcome.staleness}, not FRESH"
    if outcome.compile_state != "VALID":
        return f"compile state after refresh is {outcome.compile_state}, not VALID"
    return None


def _scalar(lines, sentinel):
    for line in lines:
        if line.startswith(sentinel + "|"):
            return line.split("|", 1)[1].strip()
    return None


def plan_sql(steps, slurm_job_id=None):
    """Everything --execute would send, in order, with RUN_ID left symbolic."""
    parts = [NEXT_RUN_ID_SQL, UNFINISHED_SQL]
    parts += [refresh_block(step, run_id=None, slurm_job_id=slurm_job_id) for step in steps]
    return "\n".join(parts)


def execute(steps, dsn, log, slurm_job_id=None, runner=None):
    """Refresh `steps` in order; return (succeeded, outcomes)."""
    kwargs = {} if runner is None else {"runner": runner}

    def sql(text):
        return run_sqlplus(text, dsn, **kwargs).lines

    lines = sql(NEXT_RUN_ID_SQL + "\n" + UNFINISHED_SQL)
    run_id = _scalar(lines, RUN_ID_SENTINEL)
    if run_id is None or not run_id.isdigit():
        log(f"ERROR could not allocate a RUN_ID: {' / '.join(lines)[-500:]}")
        return False, []
    run_id = int(run_id)
    for line in lines:
        if line.startswith(UNFINISHED_SENTINEL + "|"):
            _, prev_run, mv, started = line.split("|", 3)
            log(f"WARNING run {prev_run} left {mv} STARTED at {started} and never finished")

    log(f"run {run_id}: {len(steps)} materialized views, in dependency order")
    outcomes = []
    for index, step in enumerate(steps):
        log(f"[{index + 1}/{len(steps)}] order {step.order_no}, level {step.level_no}: "
            f"{step.mv_name} ...")
        outcome = parse_result(step.mv_name, sql(refresh_block(step, run_id, slurm_job_id)))
        problem = check(outcome)
        if problem:
            outcome.status, outcome.message = "ERROR", "CHECK: " + problem
            if outcome.log_id is not None:
                sql(check_failed_sql(outcome.log_id, problem))
        if outcome.rows_after is None and outcome.status == "OK":
            log(f"  WARNING no refresh statistics for {step.mv_name}; row checks skipped")
        outcomes.append(outcome)
        log(
            f"  {outcome.status} rows {outcome.rows_before} -> {outcome.rows_after} "
            f"{outcome.staleness}/{outcome.compile_state} {outcome.duration_sec}s"
            + (f" {outcome.message}" if outcome.message else "")
        )
        if outcome.status != "OK":
            rest = steps[index + 1:]
            if rest:
                reason = f"halted: {step.mv_name} failed in run {run_id}"
                done = _scalar(sql(skip_sql(rest, run_id, reason, slurm_job_id)),
                               SKIPPED_SENTINEL)
                if done != str(len(rest)):
                    log(f"ERROR could not log the {len(rest)} skipped MVs as SKIPPED")
                outcomes += [Outcome(s.mv_name, "SKIPPED", message=reason) for s in rest]
                log(f"  halted; {len(rest)} MVs SKIPPED")
            return False, outcomes
    return True, outcomes
