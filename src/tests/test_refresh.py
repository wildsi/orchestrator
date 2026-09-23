import subprocess

import pytest

import refresh
import run_refresh
from refresh import Outcome, Step

STEPS = [Step(1, 0, "MV_A"), Step(2, 0, "MV_B"), Step(3, 1, "MV_C")]
DSN = "user/secret@host/service"


class FakeSqlplus:
    """Stands in for subprocess.run; answers each script by what it contains."""

    def __init__(self, results=None, run_id="7", unfinished=()):
        self.results = results or {}
        self.run_id = run_id
        self.unfinished = unfinished
        self.calls = []

    def __call__(self, argv, **kwargs):
        input = kwargs["input"]  # noqa: A001 - subprocess.run's own keyword
        self.calls.append((argv, input))
        out = []
        if "NVL(MAX(run_id)" in input:
            out.append(f"{refresh.RUN_ID_SENTINEL}|{self.run_id}")
            out += [f"{refresh.UNFINISHED_SENTINEL}|{u}" for u in self.unfinished]
        elif "DBMS_MVIEW.REFRESH" in input:
            mv = input.split("list => '")[1].split("'")[0]
            out.append(self.results.get(mv, ok_line()))
        elif "'SKIPPED'" in input:
            out.append(f"{refresh.SKIPPED_SENTINEL}|{input.count(chr(39) + 'SKIPPED' + chr(39))}")
        elif refresh.CHECKED_SENTINEL in input:
            out.append(f"{refresh.CHECKED_SENTINEL}|1")
        return subprocess.CompletedProcess(argv, 0, "\n".join(out) + "\n", "")

    def refreshed(self):
        return [i.split("list => '")[1].split("'")[0] for _, i in self.calls
                if "DBMS_MVIEW.REFRESH" in i]


def ok_line(log_id=1, before=100, after=100, stale="FRESH", compile_state="VALID"):
    return f"{refresh.RESULT_SENTINEL}|{log_id}|OK|{before}|{after}|{stale}|{compile_state}||1,5|"


def run(fake):
    messages = []
    succeeded, outcomes = refresh.execute(STEPS, DSN, messages.append, "123", runner=fake)
    return succeeded, outcomes, messages


def test_all_ok_refreshes_every_mv_in_order():
    fake = FakeSqlplus()
    succeeded, outcomes, _ = run(fake)
    assert succeeded
    assert fake.refreshed() == ["MV_A", "MV_B", "MV_C"]
    assert [o.status for o in outcomes] == ["OK", "OK", "OK"]
    assert outcomes[0].duration_sec == 1.5  # decimal comma from a German NLS session


def test_a_failed_refresh_halts_and_logs_the_rest_skipped():
    error = f"{refresh.RESULT_SENTINEL}|2|ERROR||||VALID|-942|0,02|ORA-00942: table missing"
    fake = FakeSqlplus({"MV_B": error})
    succeeded, outcomes, messages = run(fake)
    assert not succeeded
    assert fake.refreshed() == ["MV_A", "MV_B"]  # MV_C never refreshed
    assert [(o.mv_name, o.status) for o in outcomes] == [
        ("MV_A", "OK"), ("MV_B", "ERROR"), ("MV_C", "SKIPPED"),
    ]
    assert outcomes[1].error_code == -942
    skip_script = next(i for _, i in fake.calls if "'SKIPPED'" in i)
    assert "'MV_C'" in skip_script and "halted: MV_B failed in run 7" in skip_script
    assert any("1 MVs SKIPPED" in m for m in messages)


def test_a_missing_result_line_is_a_failure_not_a_success():
    fake = FakeSqlplus({"MV_A": "ORA-06550: line 1, column 7: PLS-00201"})
    succeeded, outcomes, _ = run(fake)
    assert not succeeded
    assert outcomes[0].status == "ERROR"
    assert "no result line" in outcomes[0].message
    assert fake.refreshed() == ["MV_A"]


@pytest.mark.parametrize("line, reason", [
    (ok_line(after=0), "empty after refresh"),
    (ok_line(before=1000, after=400), "rows fell from 1000 to 400"),
    (ok_line(stale="UNUSABLE"), "staleness after refresh is UNUSABLE"),
    (ok_line(compile_state="NEEDS_COMPILE"), "compile state after refresh is NEEDS_COMPILE"),
])
def test_a_refresh_that_ran_can_still_fail_its_checks(line, reason):
    fake = FakeSqlplus({"MV_A": line})
    succeeded, outcomes, _ = run(fake)
    assert not succeeded
    assert outcomes[0].status == "ERROR" and reason in outcomes[0].message
    check_script = next(i for _, i in fake.calls if refresh.CHECKED_SENTINEL in i)
    assert "'CHECK: " in check_script and reason in check_script
    assert [o.status for o in outcomes[1:]] == ["SKIPPED", "SKIPPED"]


def test_missing_refresh_statistics_skip_the_row_checks_but_warn():
    fake = FakeSqlplus({"MV_A": ok_line(before="", after="")})
    succeeded, _, messages = run(fake)
    assert succeeded
    assert any("no refresh statistics for MV_A" in m for m in messages)


def test_a_row_drop_within_the_ratio_passes():
    assert refresh.check(Outcome("X", "OK", rows_before=1000, rows_after=600,
                                 staleness="FRESH", compile_state="VALID")) is None


def test_unfinished_rows_from_an_earlier_run_are_reported():
    fake = FakeSqlplus(unfinished=["6|MV_X|2026-09-20 03:00:00"])
    _, _, messages = run(fake)
    assert any("run 6 left MV_X STARTED" in m for m in messages)


def test_no_run_id_means_nothing_is_refreshed():
    fake = FakeSqlplus(run_id="ORA-00904: invalid identifier")
    succeeded, outcomes, _ = run(fake)
    assert not succeeded and outcomes == []
    assert fake.refreshed() == []


def test_the_credential_goes_on_stdin_never_in_argv():
    fake = FakeSqlplus()
    run(fake)
    for argv, stdin in fake.calls:
        assert DSN not in " ".join(argv)
        assert f"CONNECT {DSN}" in stdin


def test_every_refresh_is_complete_and_atomic():
    block = refresh.refresh_block(STEPS[0], run_id=1)
    assert "method => 'C', atomic_refresh => TRUE" in block
    assert "atomic_refresh => FALSE" not in block


def test_the_log_row_is_written_started_before_the_refresh():
    block = refresh.refresh_block(STEPS[0], run_id=1)
    assert block.index("'STARTED'") < block.index("COMMIT") < block.index("DBMS_MVIEW.REFRESH")


def test_names_that_are_not_plain_identifiers_are_refused():
    with pytest.raises(ValueError):
        refresh.refresh_block(Step(1, 0, "MV_A'; DROP TABLE x; --"), run_id=1)


def test_the_dry_run_sends_nothing_and_needs_no_credential():
    def forbidden(*args, **kwargs):
        raise AssertionError("the dry run must not start sqlplus")

    printed = []
    assert run_refresh.main([], env={}, runner=forbidden, out=printed.append) == 0
    text = "\n".join(printed)
    assert "DRY RUN" in text and text.count("DBMS_MVIEW.REFRESH(") == 25
    assert "<RUN_ID>" in text


def test_the_dry_run_prints_parents_before_children():
    printed = []
    run_refresh.main([], env={}, out=printed.append)
    text = "\n".join(printed)
    assert text.index("list => 'MV_PMC_WITH_ANNOTATIONS'") < text.index(
        "list => 'MV_00_JOIN_ENA_PMC'")


def test_only_one_mv_names_the_mvs_it_leaves_behind():
    printed = []
    run_refresh.main(["--only", "MV_00_JOIN_ENA_PMC"], env={}, out=printed.append)
    text = "\n".join(printed)
    assert text.count("DBMS_MVIEW.REFRESH(") == 1
    assert "leaves 7 MVs that read it behind it" in text


def test_execute_without_a_credential_fails_before_any_sql():
    from settings import ConfigurationError

    with pytest.raises(ConfigurationError):
        run_refresh.main(["--execute"], env={}, runner=None, out=lambda _: None)


def test_progress_counts_within_the_run_not_the_chain():
    steps = [Step(10, 0, "MV_A")]
    messages = []
    refresh.execute(steps, DSN, messages.append, runner=FakeSqlplus())
    assert any(m.startswith("[1/1] order 10, level 0: MV_A") for m in messages)

