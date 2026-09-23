import pytest

from settings import ConfigurationError, Settings


def test_the_dsn_variable_wins():
    assert Settings({"ORCH_ORACLE_DSN": "a/b@c"}).oracle_dsn == "a/b@c"


def test_the_credentials_file_first_line_is_the_dsn(tmp_path):
    creds = tmp_path / "orch.cred"
    creds.write_text("u/p@host/svc\nignored\n")
    assert Settings({"ORCH_ORACLE_CREDENTIALS_FILE": str(creds)}).oracle_dsn == "u/p@host/svc"


def test_no_credential_is_a_hard_failure_never_a_fallback():
    with pytest.raises(ConfigurationError):
        Settings({}).oracle_dsn  # noqa: B018


def test_importing_and_constructing_needs_no_credential():
    Settings({})
