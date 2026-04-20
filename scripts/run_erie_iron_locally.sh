#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="${VENV_DIR:-.venv}"
POSTGRES_FORMULA="${POSTGRES_FORMULA:-postgresql@17}"
POSTGRES_MAJOR="${POSTGRES_FORMULA#postgresql@}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
DEFAULT_LOCAL_DB_NAME="erieiron_v1"
LOCAL_DB_NAME="${LOCAL_DB_NAME:-}"
SKIP_RUNSERVER=false
SKIP_SCHEMA_SETUP=false
RUNSERVER_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-runserver)
            SKIP_RUNSERVER=true
            shift
            ;;
        --skip-schema-setup)
            SKIP_SCHEMA_SETUP=true
            shift
            ;;
        *)
            RUNSERVER_ARGS+=("$1")
            shift
            ;;
    esac
done

print_info() {
    printf '[INFO] %s\n' "$1"
}

print_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

require_command() {
    local command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        print_error "Required command not found: $command_name"
        exit 1
    fi
}

runserver_addrport_arg() {
    local expects_value=false
    for arg in "${RUNSERVER_ARGS[@]}"; do
        if [[ "$expects_value" == "true" ]]; then
            expects_value=false
            continue
        fi

        case "$arg" in
            --settings|--pythonpath|--verbosity|-s|-p|-v)
                expects_value=true
                ;;
            --settings=*|--pythonpath=*|--verbosity=*)
                ;;
            --ipv6|--nothreading|--noreload|--nostatic|--insecure|--skip-checks|--traceback|--no-color|--force-color|--help|--version)
                ;;
            -*)
                ;;
            *)
                printf '%s\n' "$arg"
                return 0
                ;;
        esac
    done
    return 1
}

extract_runserver_port() {
    local addrport="$1"
    local port="${addrport##*:}"
    if [[ "$addrport" != *:* ]]; then
        port="$addrport"
    fi

    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        print_error "Unable to determine Django port from runserver addrport: ${addrport}"
        exit 1
    fi

    printf '%s\n' "$port"
}

port_is_available() {
    local port="$1"
    "$PYTHON_BOOTSTRAP_BIN" - "$port" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

find_available_webapp_port() {
    local port=8001
    while [[ "$port" -le 65535 ]]; do
        if port_is_available "$port"; then
            printf '%s\n' "$port"
            return 0
        fi
        port=$((port + 1))
    done

    print_error "No available Django port found above 8000."
    exit 1
}

read_config_webapp_port() {
    local runtime_path="$1"
    "$PYTHON_BOOTSTRAP_BIN" - "$runtime_path" <<'PY'
import json
import sys
from pathlib import Path

config_payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
webapp_port = config_payload.get("WEBAPP_PORT")
if webapp_port in (None, ""):
    raise SystemExit(1)
print(int(webapp_port))
PY
}

resolve_webapp_port() {
    local runtime_path="$1"
    local cli_addrport
    cli_addrport="$(runserver_addrport_arg || true)"
    if [[ -n "$cli_addrport" ]]; then
        extract_runserver_port "$cli_addrport"
        return 0
    fi

    local config_port
    if config_port="$(read_config_webapp_port "$runtime_path")"; then
        printf '%s\n' "$config_port"
        return 0
    fi

    find_available_webapp_port
}

wait_for_postgres() {
    local attempts=0
    until pg_isready -h localhost -p "${POSTGRES_PORT}" >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [[ "$attempts" -ge 30 ]]; then
            print_error "Postgres did not become ready on localhost:${POSTGRES_PORT}"
            exit 1
        fi
        sleep 1
    done
}

database_exists() {
    local escaped_db_name="${LOCAL_DB_NAME//\'/\'\'}"

    psql \
        -h localhost \
        -p "${POSTGRES_PORT}" \
        -d postgres \
        -Atqc "SELECT 1 FROM pg_database WHERE datname = '${escaped_db_name}'" | grep -q '^1$'
}

resolve_local_db_name() {
    local config_path="$1"

    if [[ -n "${LOCAL_DB_NAME}" ]]; then
        return
    fi

    LOCAL_DB_NAME="$(
        python - "${config_path}" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
config_payload = json.loads(config_path.read_text(encoding="utf-8"))
configured_db_name = str(config_payload.get("LOCAL_DB_NAME", "")).strip()
print(configured_db_name)
PY
    )"

    if [[ -z "${LOCAL_DB_NAME}" ]]; then
        LOCAL_DB_NAME="${DEFAULT_LOCAL_DB_NAME}"
    fi
}

ensure_local_database() {
    if ! database_exists; then
        print_info "Creating local Postgres database ${LOCAL_DB_NAME}"
        createdb -h localhost -p "${POSTGRES_PORT}" "${LOCAL_DB_NAME}"
    fi

    print_info "Ensuring pgvector is installed in ${LOCAL_DB_NAME}"
    psql \
        -h localhost \
        -p "${POSTGRES_PORT}" \
        -d "${LOCAL_DB_NAME}" \
        -v ON_ERROR_STOP=1 \
        -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
}

ensure_local_runtime_json_file() {
    local runtime_path="$1"
    local example_path="$2"

    if [[ ! -f "${runtime_path}" ]]; then
        cp "${example_path}" "${runtime_path}"
        print_error "Created ${runtime_path} from the example template."
        print_error "Replace every 'replace-me' value in ${runtime_path}, then rerun this script."
        exit 1
    fi

    if grep -q 'replace-me' "${runtime_path}"; then
        print_error "${runtime_path} still contains placeholder values."
        print_error "Replace every 'replace-me' value in ${runtime_path}, then rerun this script."
        exit 1
    fi
}

ensure_local_runtime_files() {
    ensure_local_runtime_json_file conf/config.json conf/config.example.json
    ensure_local_runtime_json_file conf/secrets.json conf/secrets.example.json
}

ensure_current_database_schema() {
    print_info "Merging Django migration conflicts when needed"
    python manage.py makemigrations --merge --noinput

    print_info "Generating Django migration files"
    python manage.py makemigrations --noinput

    print_info "Applying Django migrations"
    python manage.py migrate
}

active_postgres_major() {
    if ! pg_isready -h localhost -p "${POSTGRES_PORT}" >/dev/null 2>&1; then
        return 1
    fi
    psql -h localhost -p "${POSTGRES_PORT}" -d postgres -Atqc "SHOW server_version_num" | cut -c1-2
}

ensure_compatible_postgres_service() {
    local active_major
    active_major="$(active_postgres_major || true)"

    if [[ -n "${active_major}" && "${active_major}" != "${POSTGRES_MAJOR}" ]]; then
        print_info "Stopping incompatible local Postgres service postgresql@${active_major}"
        brew services stop "postgresql@${active_major}" >/dev/null 2>&1 || true
    fi

    brew services start "${POSTGRES_FORMULA}"
}

vector_extension_available() {
    [[ "$(psql -h localhost -p "${POSTGRES_PORT}" -d postgres -Atqc \
        "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")" == "1" ]]
}

require_vector_extension() {
    if ! vector_extension_available; then
        print_error "The running ${POSTGRES_FORMULA} server does not expose the pgvector extension."
        print_error "Install or reinstall Homebrew pgvector for ${POSTGRES_FORMULA}, then rerun this script."
        exit 1
    fi
}

configure_local_runtime_env() {
    unset ERIEIRON_DB_HOST
    unset ERIEIRON_DB_NAME
    unset ERIEIRON_DB_PORT
    unset RDS_SECRET_ARN
    unset COGNITO_SECRET_ARN
    unset COGNITO_USER_POOL_ID
    unset COGNITO_CLIENT_ID
    unset COGNITO_DOMAIN
    unset ERIEIRON_RUNTIME_PROFILE
    unset ERIEIRON_LOCAL_CONFIG_FILE
    unset ERIEIRON_LOCAL_SECRETS_FILE
    unset LOCAL_AUTH_ENABLED
    unset LOCAL_ADMIN_EMAIL
    unset LOCAL_AUTH_PASSWORD
    unset LOCAL_AUTH_NAME
    export LOCAL_DB_NAME
    export WEBAPP_PORT
}

if [[ ! -f manage.py ]]; then
    print_error "Run this script from the Erie Iron repo root."
    exit 1
fi

require_command brew

print_info "Installing local dependencies with Homebrew"
brew list "${POSTGRES_FORMULA}" >/dev/null 2>&1 || brew install "${POSTGRES_FORMULA}"
brew list pgvector >/dev/null 2>&1 || brew install pgvector
brew list node >/dev/null 2>&1 || brew install node

export PATH="$(brew --prefix "${POSTGRES_FORMULA}")/bin:$PATH"

ensure_compatible_postgres_service

require_command createdb
require_command pg_isready
require_command psql

wait_for_postgres
require_vector_extension

PYTHON_BOOTSTRAP_BIN="${PYTHON_BOOTSTRAP_BIN:-python3}"
if ! command -v "$PYTHON_BOOTSTRAP_BIN" >/dev/null 2>&1; then
    PYTHON_BOOTSTRAP_BIN=python
fi
require_command "$PYTHON_BOOTSTRAP_BIN"

if [[ ! -d "$VENV_DIR" ]]; then
    print_info "Creating Python virtual environment"
    "$PYTHON_BOOTSTRAP_BIN" -m venv "$VENV_DIR"
fi

source "${VENV_DIR}/bin/activate"

print_info "Installing Python dependencies"
python -m pip install -r requirements.txt

print_info "Installing Node dependencies"
npm install

print_info "Compiling UI assets"
npm run compile-ui

ensure_local_runtime_files
resolve_local_db_name "conf/config.json"
ensure_local_database

WEBAPP_PORT="$(resolve_webapp_port conf/config.json)"
print_info "Using Django development port ${WEBAPP_PORT}"

configure_local_runtime_env

if [[ "$SKIP_SCHEMA_SETUP" == "true" ]]; then
    print_info "Skipping Django schema setup per --skip-schema-setup"
else
    ensure_current_database_schema
fi

print_info "Bootstrapping local runtime"
python manage.py bootstrap_local_runtime

if [[ "$SKIP_RUNSERVER" == "true" ]]; then
    print_info "Skipping runserver per --skip-runserver"
    exit 0
fi

print_info "Starting Django development server"
if [[ -n "$(runserver_addrport_arg || true)" ]]; then
    exec python manage.py runserver "${RUNSERVER_ARGS[@]}"
fi
exec python manage.py runserver "${WEBAPP_PORT}" "${RUNSERVER_ARGS[@]}"
