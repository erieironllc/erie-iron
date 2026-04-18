$ITERATE

```
python manage.py makemigrations
python manage.py migrate
```

## Iteration 2026-04-18 15:34:07 PDT

✅ Local-profile `makemigrations` is clean.
⏸️ `migrate` remains a manual step in this shell because the repo instructions forbid executing database migrations here.

What changed:
- `manage.py` now applies `--erieiron-env` before importing `settings`, so the local profile can be selected per command.
- `erieiron_common/settings_utils.py` now ignores blank inherited env vars when the selected `.env` file has a real value.
- `erieiron_common/models.py` now uses a stable default for `PubSubHanderInstance.env`, which stops `dev_local` from generating a fake migration.

Evaluation:
- `ERIEIRON_ENV=dev_local .venv/bin/python manage.py makemigrations` -> `No changes detected`
- `.venv/bin/python manage.py --erieiron-env=dev_local makemigrations` -> `No changes detected`
- Manual next step: `.venv/bin/python manage.py --erieiron-env=dev_local migrate`

## Files touched

- `manage.py` lines 9-37 and 47-60: moved `--erieiron-env` parsing ahead of the settings import and passed Django the filtered argv.
- `erieiron_common/settings_utils.py` lines 103-108: stripped blank inherited env overrides when the active `.env` file provides a non-empty value.
- `erieiron_common/models.py` line 686: pinned `PubSubHanderInstance.env` to the committed default so local profile changes do not create new migrations.
