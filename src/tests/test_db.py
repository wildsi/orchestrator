import subprocess

import db


def test_the_client_directory_is_put_on_the_library_path():
    """Without it the fallback sqlplus dies loading libsqlplus.so (2026-09-23)."""
    env = db.client_env("/opt/Bio/oracle-instant-client/21.4/bin/sqlplus",
                        base={"LD_LIBRARY_PATH": "/usr/lib64"})
    assert env["LD_LIBRARY_PATH"] == "/opt/Bio/oracle-instant-client/21.4/bin:/usr/lib64"


def test_an_unset_library_path_gets_just_the_client_directory():
    env = db.client_env("/x/bin/sqlplus", base={})
    assert env["LD_LIBRARY_PATH"] == "/x/bin"


def test_run_sqlplus_passes_that_environment_and_keeps_the_dsn_off_argv():
    seen = {}

    def runner(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "out\n", "")

    result = db.run_sqlplus("SELECT 1 FROM dual;", "u/secret@h/s", runner=runner,
                            sqlplus="/x/bin/sqlplus")
    assert result.lines == ["out"]
    assert seen["env"]["LD_LIBRARY_PATH"].startswith("/x/bin")
    assert "u/secret@h/s" not in " ".join(seen["argv"])
    assert "CONNECT u/secret@h/s" in seen["input"]
