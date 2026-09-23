"""The database boundary: one sqlplus subprocess per call.

The credential goes on stdin as a CONNECT line, never into argv, where `ps`
would show it. Tests replace `runner` (default `subprocess.run`), so the
suite needs no database, no client and no credential.
"""

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass

# Where the oracle-instant-client modulefile puts the client. `module` is a
# shell function from the interactive profile and is absent from Slurm-less
# and tooling shells; this mirrors ../epmc_pipeline/legacy_db_env.sh.
_CLIENT_GLOB = "/opt/Bio/oracle-instant-client/*/bin"


@dataclass
class SqlResult:
    returncode: int
    output: str

    @property
    def lines(self):
        return self.output.splitlines()


def find_sqlplus():
    found = shutil.which("sqlplus")
    if found:
        return found
    for candidate in sorted(glob.glob(_CLIENT_GLOB)):
        path = os.path.join(candidate, "sqlplus")
        if os.access(path, os.X_OK):
            return path
    raise FileNotFoundError(
        "sqlplus not found. Run `module load oracle-instant-client` first."
    )


def run_sqlplus(sql_text, dsn, runner=subprocess.run, sqlplus=None):
    """Run a script and return its combined output.

    SQLERROR-exit is armed only across the CONNECT, so a bad credential fails
    fast; the script's own statements report their outcome through sentinel
    lines that the caller must find (see refresh.py). The return code alone
    is not trusted: WHENEVER SQLERROR EXIT SQL.SQLCODE returns the error
    number mod 256, so some Oracle errors exit 0.
    """
    payload = (
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        f"CONNECT {dsn}\n"
        "WHENEVER SQLERROR CONTINUE\n"
        "SET SERVEROUTPUT ON SIZE UNLIMITED FEEDBACK OFF HEADING OFF PAGESIZE 0\n"
        "SET LINESIZE 4000 TRIMOUT ON VERIFY OFF\n"
        f"{sql_text}\n"
        "EXIT;\n"
    )
    completed = runner(
        [sqlplus or find_sqlplus(), "-s", "/nolog"],
        input=payload,
        capture_output=True,
        text=True,
    )
    return SqlResult(completed.returncode, (completed.stdout or "") + (completed.stderr or ""))
