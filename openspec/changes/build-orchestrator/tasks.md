# Tasks

Nothing here has been started. Phases 0–2 touch no database.

## 0. Orient (read-only, no database)

- [ ] 0.1 Read `CLAUDE.md` here, then `../epmc_pipeline/CLAUDE.md` and
      `../ena_pipeline/CLAUDE.md`.
- [ ] 0.2 Read `../epmc_pipeline/docs/IMPROVEMENTS.md` — especially §2
      (defects found by measurement) and §7 (what is deliberately not done).
- [ ] 0.3 Read `../epmc_pipeline/db_inventory/02_materialized_views.txt`,
      `03_mv_dependencies.txt`, `05_mv_definitions.txt`.
- [ ] 0.4 Confirm both pipelines' entry points and exit-code behaviour:
      `../ena_pipeline/src/main.py`, `../epmc_pipeline/src/main.py`. Both
      return non-zero on failure — verify, do not assume.

## 1. Answer the open questions before building

- [ ] 1.1 **Is there already a backup?** Ask the DBA or check for an RMAN /
      expdp schedule. If the warehouse is already backed up adequately, the
      dump stage may reduce to a no-op and the chain gets simpler.
- [ ] 1.2 **Is fast refresh possible?** An unused `MLOG$_ENA_SEQUENCES`
      exists. Determine whether the MV definitions qualify. This changes the
      cost of the project, so settle it before optimising a complete refresh.
- [ ] 1.3 **Derive the true refresh order** from `user_dependencies`
      (read-only) and compare against the `MV_00/01/02` naming. Record any
      disagreement — the names may be wrong.
      Derived offline 2026-09-23 (3 levels, no cycle; three `MV_00_*` are
      not level 0, two `MV_*` names are plain tables): `docs/MV_STATE.md` §3.
      Left unticked until `mv_graph.py` reproduces it from the fixture.
- [ ] 1.4 **Time one complete refresh** of a large MV with
      `atomic_refresh=TRUE`, to know whether the whole chain fits a window.
      **Needs approval — this writes.**

## 2. Build (no database access)

- [ ] 2.1 Scaffold: `pyproject.toml` + uv, copy `legacy_db_env.sh` from
      `../epmc_pipeline` (it resolves credentials *and* locates the Oracle
      client when `module` is absent), `.gitignore`, `.env.example`.
- [ ] 2.2 `src/mv_graph.py` — build the refresh order by topologically
      sorting `user_dependencies`; fail loudly on a cycle. Unit-test against
      a fixture of the real dependency rows, no database.
- [ ] 2.3 `src/refresh.py` — `DBMS_MVIEW.REFRESH` per MV in order,
      `atomic_refresh=TRUE`, per-MV timing and outcome captured. Fake
      `subprocess.run` in tests.
- [ ] 2.4 `src/dump.py` — once 1.1 is answered.
- [ ] 2.5 `src/notify.py` — reuse the shape of
      `../epmc_pipeline/src/notifications.py`: two reports, two recipient
      lists, never raises.
- [ ] 2.6 The sbatch chain and a `submit.sh` that wires the dependencies:
      ```
      ENA=$(sbatch --parsable  ena.sbatch)
      EPMC=$(sbatch --parsable epmc.sbatch)
      DUMP=$(sbatch --parsable --dependency=afterok:$ENA:$EPMC dump.sbatch)
      sbatch --dependency=afterok:$DUMP refresh.sbatch
      ```
- [ ] 2.7 Verify: `uv run pytest`, `uv run ruff check src`, and a dry-run
      mode that prints every statement it would issue without issuing any.

## 3. Validate (needs approval per step)

- [x] 3.1 Recompile the 15 INVALID MVs as a **separate prior step**. Do not
      fold pre-existing breakage into the first orchestrated run.
      Done 2026-09-23 from `../epmc_pipeline` (its task 4.1); all VALID.
      Result and the UNUSABLE finding: `docs/MV_STATE.md`.
- [ ] 3.2 Refresh one leaf MV end to end. Confirm it stays readable
      throughout and that APEX is unaffected.
- [ ] 3.3 Full chain on a quiet day, watched.
- [ ] 3.4 Hand the schedule over; retire the two standalone sbatch triggers.

## Out of scope

The dashboard over `V_PIPELINE_EXECUTIONS`, and rewriting MV queries against
the new normalised `EPMC_*` tables (that is the MV-optimisation project — the
compatibility views exist so the two can be separated).
