# Run Erie Iron Locally on a Mac Mini

Fast path:

```bash
./scripts/run_erie_iron_locally.sh
```

The script automates the setup flow below, uses a local Postgres service that exposes `pgvector`, creates the local database if needed, runs Django migrations if needed, and starts `runserver`. It stops early only if `conf/local_secrets.json` still contains the example placeholder values.

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

3. Create the local secrets file and replace the placeholder values.

```bash
cp conf/local_secrets.example.json conf/local_secrets.json
```

4. Use the local runtime profile.

```bash
export ERIEIRON_ENV=dev_local
```

5. Run the Django migrations.

```bash
python manage.py migrate
```

6. Verify the local runtime and create the default admin identity.

```bash
python manage.py bootstrap_local_runtime
```

7. Start the app.

```bash
python manage.py runserver
```

8. Sign in with the local email from `conf/.env.dev_local` and the password you configured there.

The local profile uses:

- Postgres on `localhost:5432` via `postgresql@17`
- `conf/local_secrets.json` instead of AWS Secrets Manager
- local session-based login instead of Cognito
- `python manage.py bootstrap_local_runtime --verify-only` to re-check the setup without changing users
