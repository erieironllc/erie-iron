# Erie Iron

Erie Iron is a local-first agent runtime for building, running, and improving autonomous workflows.

The project is centered on three ideas:

- run locally on a Mac mini or similar always-on machine
- define workflows in the database instead of hardcoded Python graphs
- make tasks measurable and self-improving through prompt history, execution audits, and evaluation scores

## Product Goal

Erie Iron should let an operator:

- run the system locally with local PostgreSQL
- create and manage multiple named workflows through metadata and UI
- route work through workflow steps, triggers, and connections stored in the database
- give each task a system prompt, prompt version history, and execution audit trail
- evaluate task outcomes on a `0` to `1` scale
- improve task prompts over time, either automatically or through the UI

## Repository Layout

- `erieiron_common/`
  Shared enums, LLM adapters, AWS helpers, messaging, local runtime helpers, and other common utilities.
- `erieiron_autonomous_agent/`
  Workflow orchestration, coding agents, task models, workflow models, management commands, and iteration logic.
- `erieiron_ui/`
  Django views, templates, Backbone assets, and Sass styles for the operator UI.
- `docs/`
  Architecture notes, UI rules, local runtime documentation, and design docs.
- `scripts/`
  Helper scripts, including the local bootstrap path in `scripts/run_erie_iron_locally.sh`.

## Core Runtime Model

- local Django application for the UI and operator workflows
- local PostgreSQL as the primary data store
- database-backed workflow definitions for steps, triggers, and connections
- task execution records that capture prompt version, model, and outcome
- prompt improvement flows that use prior evaluations and execution history
- shared LLM adapters, messaging, and agent orchestration in the existing modules

## Local Development

Fast path:

```bash
./scripts/run_erie_iron_locally.sh
```

Before running that script:

1. Copy `conf/config.example.json` to `conf/config.json`.
2. Copy `conf/secrets.example.json` to `conf/secrets.json`.
3. Replace every `replace-me` value in both files.

The local profile uses:

- PostgreSQL on `localhost:5432`
- `conf/config.json` for local platform configuration
- `conf/secrets.json` instead of AWS Secrets Manager
- local session-based auth instead of Cognito

Detailed setup notes live in [docs/run_erie_iron_locally.md](docs/run_erie_iron_locally.md).

If you want to run the steps manually instead of using the helper script:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm run compile-ui
cp conf/config.example.json conf/config.json
cp conf/secrets.example.json conf/secrets.json
python manage.py migrate
python manage.py bootstrap_local_runtime
python manage.py runserver
```

If `LOCAL_AUTH_ENABLED` is `true`, sign in with the local email from `conf/config.json` and the password from `conf/secrets.json`. If it is `false`, Erie Iron auto-signs in as `LOCAL_ADMIN_EMAIL`.

## Workflow Model

Workflows are organized as reusable named definitions with:

- multiple named workflows
- steps, triggers, and connections stored in the database
- UI for creating and editing workflows
- task routing driven by workflow metadata

## Self-Improving Tasks

Each task is expected to support:

- a system prompt
- prompt version history
- an evaluation method that returns a score from `0` to `1`
- an execution audit trail that records prompt version, model, and outcome
- scheduled or operator-managed prompt improvement based on task history

## Useful Entry Points

- `python manage.py runserver`
- `python manage.py bootstrap_local_runtime`
- `python manage.py message_processor_daemon`
- `npm run compile-ui`

## Related Docs

- [docs/architecture.md](docs/architecture.md)
- [docs/high_level_architecture.md](docs/high_level_architecture.md)
- [docs/ui_spec.md](docs/ui_spec.md)
- [docs/run_erie_iron_locally.md](docs/run_erie_iron_locally.md)

## License

MIT
