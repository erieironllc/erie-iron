# Run Erie Iron Locally on a Mac Mini

Fast path:

```bash
./scripts/run_erie_iron_locally.sh
```

The script automates the setup flow below, uses a local Postgres service that exposes `pgvector`, creates the local database if needed, verifies that Django migration files are present and already applied, and starts `runserver`. It stops early only if `conf/config.json` or `conf/secrets.json` still contains the example placeholder values or if you still need to run migrations manually. When `WEBAPP_PORT` is omitted from `conf/config.json`, the script chooses the first available port above `8000`.

1. Install the local dependencies.

```bash
brew install postgresql@17 pgvector node
brew services start postgresql@17
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm run compile-ui
```

2. Create the local Postgres database.

```bash
createdb erieiron_local
```

3. Create the local runtime files and replace the placeholder values, including the application repo URL for this Erie Iron instance.

```bash
cp conf/config.example.json conf/config.json
cp conf/secrets.example.json conf/secrets.json
```

4. Confirm `conf/config.json` still has `"ERIEIRON_RUNTIME_PROFILE": "local"`.
5. Keep `BASE_URL` host-only, and set `WEBAPP_PORT` when you want a fixed Django port.

6. Run the Django migrations.

```bash
python manage.py migrate
```

7. Verify the local runtime and create the default admin identity.

```bash
python manage.py bootstrap_local_runtime
```

8. Start the app.

```bash
python manage.py runserver 8000
```

9. Sign in with the local email from `conf/config.json` and the password from `conf/secrets.json`.

If `LOCAL_AUTH_ENABLED` is set to `false` in `conf/config.json`, Erie Iron auto-signs in as `LOCAL_ADMIN_EMAIL` and skips the login screen.

The local profile uses:

- Postgres on `localhost:5432` via `postgresql@17`
- `conf/config.json` for local platform configuration such as `APPLICATION_REPO`
- `conf/secrets.json` instead of AWS Secrets Manager
- local session-based login instead of Cognito
- `python manage.py bootstrap_local_runtime --verify-only` to re-check the setup without changing users
