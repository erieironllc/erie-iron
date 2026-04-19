# 2026-04-19 14:12:21
$GAP-IMPL - added workflow long-term-memory/datastore flags, default-SQLite isolated datastore configuration, and admin workflow controls plus regression coverage.

Suggested next step: apply the new workflow migration and rerun the workflow model/admin test targets in an environment where the shared pytest database is not already in use.


# 2026-04-19 12:59:02
$ITERATE - ran the local bootstrap script, fixed the `WEBAPP_PORT` environment bug in the launcher, and got Django running locally on port 8000.

Suggested next step: open `http://127.0.0.1:8000/` in a browser and confirm the local login flow or auto-login behavior.


# 2026-04-19 12:45:21
$IMPL - added WEBAPP_PORT-based local URL handling, dynamic local port selection, and shared runtime URL helpers for Erie Iron.

Suggested next step: run `./scripts/run_erie_iron_locally.sh` once to confirm the selected port matches generated links and browser access.
