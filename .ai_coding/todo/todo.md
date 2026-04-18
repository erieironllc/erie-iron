$ITERATE

the script should just run the migrations if needed (also create the db if needed)

```
bash scripts/run_erie_iron_locally.sh
```

## 2026-04-18 13:29:28 Iteration

Changed:
- Updated `scripts/run_erie_iron_locally.sh` so it tracks the local database name, creates the local database if needed, and runs `python manage.py migrate --noinput` when the local database has not been migrated yet.
- Updated `docs/run_erie_iron_locally.md` so the documented fast path says the script creates the database and runs migrations automatically.

Evaluation:
- `bash -n scripts/run_erie_iron_locally.sh` passed.
- I did not run `bash scripts/run_erie_iron_locally.sh` after this change because the updated script would now execute Django migrations, and the active repo instructions for this workflow forbid me from running database migrations in this shell.

Help needed:
- Run `bash scripts/run_erie_iron_locally.sh` yourself to validate the new migration behavior, or explicitly tell me to override the no-migrations rule for this workflow.

## Files touched
- `scripts/run_erie_iron_locally.sh` (lines 8, 54-80, 84-100, 145-154): used the local DB name consistently, kept local env scrubbing, created the DB if needed, and ran migrations automatically when `django_migrations` is absent.
- `docs/run_erie_iron_locally.md` (lines 9, 16-17, 41): updated the fast-path documentation to match the `.venv` workflow and automatic migration behavior.

## 2026-04-18 13:35:28 Iteration

Changed:
- Updated `erieiron_autonomous_agent/migrations/0006_codeversion_codebert_embedding.py` so the first historical vector migration creates the `vector` extension before adding the `VectorField`.
- Updated `scripts/run_erie_iron_locally.sh` to target a Postgres service compatible with Homebrew `pgvector`, verify that the running server exposes the `vector` extension, and keep creating the local database plus running migrations automatically when needed.
- Updated `docs/run_erie_iron_locally.md` so the local setup instructions use `postgresql@17` plus `pgvector`, matching the script and the extension requirement.

Evaluation:
- `bash -n scripts/run_erie_iron_locally.sh` passed.
- `./.venv/bin/python -m py_compile erieiron_autonomous_agent/migrations/0006_codeversion_codebert_embedding.py` passed.
- The root cause of the reported migration failure was twofold:
  - migration `0006` added a `VectorField` before any migration created the `vector` extension
  - the current local runner was using `postgresql@16`, while this machine's Homebrew `pgvector` install only exposes extension files for Postgres 14 and 17
- I did not run `bash scripts/run_erie_iron_locally.sh` after this change because the script now runs migrations and the active repo instructions for this workflow still forbid me from executing migrations in this shell.

Help needed:
- Run `bash scripts/run_erie_iron_locally.sh` yourself to validate the new migration path on `postgresql@17`, or explicitly tell me to override the no-migrations rule for this workflow.

## Files touched
- `erieiron_autonomous_agent/migrations/0006_codeversion_codebert_embedding.py` (lines 3-19): created the `vector` extension before adding the first vector column so fresh databases can migrate cleanly.
- `scripts/run_erie_iron_locally.sh` (lines 8-10, 45-119, 147-161, 185-194): selected a pgvector-compatible Postgres formula, checked extension availability, used the configured local DB name consistently, and kept automatic DB creation plus migrations.
- `docs/run_erie_iron_locally.md` (lines 9, 14-15, 63): aligned the local setup instructions with `postgresql@17` and `pgvector`.
