# 2026-04-18 13:35:28
$ITERATE fixed the first pgvector migration to create the extension before adding vector columns and updated the local runner/docs to use a Postgres service that actually exposes pgvector on this machine.

Run `bash scripts/run_erie_iron_locally.sh` manually to validate the new migration path, because the active repo instructions for this workflow still forbid me from executing migrations in this shell.


# 2026-04-18 13:29:28
$ITERATE changed the local runner to create the local database and auto-run Django migrations when needed, and updated the local-run doc to match.

Run `bash scripts/run_erie_iron_locally.sh` manually to validate the new migration path, because the active repo instructions for this workflow forbid me from executing migrations in this shell.


# 2026-04-18 13:17:21
$ITERATE fixed the local runner to use `.venv`, scrub inherited remote runtime variables, avoid AWS-backed database settings in local mode, and stop at the manual migration prerequisite.

Run `python manage.py migrate` with `ERIEIRON_ENV=dev_local`, then rerun `bash scripts/run_erie_iron_locally.sh` to continue the local bootstrap.


# 2026-04-18 12:43:42
$ITERATE updated the local runner to require manual migrations, verified the shell syntax, and ran it until it stopped on placeholder values in `conf/local_secrets.json`.

Fill in `conf/local_secrets.json`, run `python manage.py migrate` manually with `ERIEIRON_ENV=dev_local`, and then rerun `bash scripts/run_erie_iron_locally.sh`.


# 2026-04-18 12:18:29
$LINEAR_IMPL implemented ERI-5, ERI-6, ERI-8, and ERI-9 with a real local runtime profile, local secret loading, local login, and a bootstrap/verification command plus docs.

Manually run the Mac Mini bootstrap flow against a real local Postgres instance and review the staged changes, because only request-level and unit validation was possible in this shell.


# 2026-04-18 11:56:15
$LINEAR_UPDATE created 4 Linear projects and 16 backlog issues for the `Erie Iron, LLC` team from `goal.md`; next step is to review the roadmap in Linear and start implementation with `ERI-5`.

Review the new Linear projects and start implementation with `ERI-5` because it establishes the local runtime foundation the other roadmap items build on.


# 2026-04-18 11:51:37
$LINEAR_UPDATE previewed 4 new Linear projects and 16 new backlog issues for the `Erie Iron, LLC` team; no Linear writes were applied because explicit confirmation is still required.

Reply `confirm` to apply the previewed Linear changes, or `cancel` to leave Linear unchanged.
