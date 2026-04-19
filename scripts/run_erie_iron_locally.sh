#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="${VENV_DIR:-.venv}"
POSTGRES_FORMULA="${POSTGRES_FORMULA:-postgresql@17}"
POSTGRES_MAJOR="${POSTGRES_FORMULA#postgresql@}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
LOCAL_DB_NAME="${LOCAL_DB_NAME:-erieiron_local}"

SKIP_RUNSERVER=false
RUNSERVER_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-runserver)
            SKIP_RUNSERVER=true
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
    psql -h localhost -p "${POSTGRES_PORT}" -d postgres -Atqc \
        "SELECT 1 FROM pg_database WHERE datname = '${LOCAL_DB_NAME}'" | grep -q '^1$'
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
    print_info "Checking Django migration files"
    if ! python manage.py makemigrations --check --dry-run >/dev/null; then
        print_error "Model changes are missing migration files."
        print_error "Run 'python manage.py makemigrations' manually, review the result, and rerun this script."
        exit 1
    fi

    print_info "Checking for unapplied Django migrations"
    if python manage.py showmigrations --plan | grep -q '\[ \]'; then
        print_error "The local database has unapplied migrations."
        print_error "Run 'python manage.py migrate' manually, then rerun this script."
        exit 1
    fi
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

if ! database_exists; then
    print_info "Creating local Postgres database ${LOCAL_DB_NAME}"
    createdb -h localhost -p "${POSTGRES_PORT}" "${LOCAL_DB_NAME}"
fi

ensure_local_runtime_files

configure_local_runtime_env

ensure_current_database_schema

print_info "Bootstrapping local runtime"
python manage.py bootstrap_local_runtime

if [[ "$SKIP_RUNSERVER" == "true" ]]; then
    print_info "Skipping runserver per --skip-runserver"
    exit 0
fi

print_info "Starting Django development server"
exec python manage.py runserver "${RUNSERVER_ARGS[@]}"
