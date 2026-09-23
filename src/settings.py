"""Environment-backed settings.

The DSN is a property, so importing this module never fails on a machine
without credentials - the dry run and the tests need none.

Resolution order, identical to ../epmc_pipeline/src/settings.py:
  1. ORCH_ORACLE_DSN
  2. first line of the file named by ORCH_ORACLE_CREDENTIALS_FILE
  3. hard failure - never a literal fallback
"""

import os


class ConfigurationError(RuntimeError):
    pass


class Settings:
    def __init__(self, env=None):
        self._env = os.environ if env is None else env

    @property
    def oracle_dsn(self):
        dsn = self._env.get("ORCH_ORACLE_DSN")
        if dsn:
            return dsn

        creds_path = self._env.get("ORCH_ORACLE_CREDENTIALS_FILE")
        if creds_path and os.path.exists(creds_path):
            with open(creds_path) as creds_file:
                first_line = creds_file.readline().strip()
            if first_line:
                return first_line

        raise ConfigurationError(
            "Oracle connection DSN is not configured. Set ORCH_ORACLE_DSN or "
            "ORCH_ORACLE_CREDENTIALS_FILE to a chmod 600 file whose first line is "
            "the DSN (user/password@host/service)."
        )

    @property
    def slurm_job_id(self):
        return self._env.get("SLURM_JOB_ID")
