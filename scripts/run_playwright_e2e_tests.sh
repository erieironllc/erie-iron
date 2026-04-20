#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG_PATH="conf/config.json"
DEFAULT_HOST="${PLAYWRIGHT_E2E_HOST:-127.0.0.1}"
DEFAULT_BROWSER="${PLAYWRIGHT_E2E_BROWSER:-chromium}"

HOST="$DEFAULT_HOST"
PORT="${PLAYWRIGHT_E2E_PORT:-}"
BROWSER="$DEFAULT_BROWSER"
SKIP_BROWSER_INSTALL=false
PYTEST_ARGS=()

print_info() {
    printf '[INFO] %s\n' "$1"
}

print_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

usage() {
    cat <<EOF
Usage: ./scripts/run_playwright_e2e_tests.sh [options] [-- <pytest args>]

Options:
  --host <host>                 Django host to bind (default: ${DEFAULT_HOST})
  --port <port>                 Django port to bind (default: WEBAPP_PORT from ${CONFIG_PATH})
  --browser <browser>           Playwright browser for pytest-playwright (default: ${DEFAULT_BROWSER})
  --skip-browser-install        Skip python -m playwright install
  -h, --help                    Show this help text

Examples:
  ./scripts/run_playwright_e2e_tests.sh
  ./scripts/run_playwright_e2e_tests.sh -- --headed -k root_chat
EOF
}

require_command() {
    local command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        print_error "Required command not found: $command_name"
        exit 1
    fi
}

require_running_erie_iron() {
    local health_url="$1"
    if curl --silent --fail "$health_url" >/dev/null 2>&1; then
        return 0
    fi

    print_error "Erie Iron is not responding at ${health_url}"
    print_error "Start the app locally first, then rerun this script."
    exit 1
}

read_config_webapp_port() {
    local config_path="$1"
    python - "$config_path" <<'PY'
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

resolve_port() {
    if [[ -n "$PORT" ]]; then
        printf '%s\n' "$PORT"
        return 0
    fi

    if read_config_webapp_port "$CONFIG_PATH"; then
        return 0
    fi

    print_error "Unable to determine the Django port."
    print_error "Set PLAYWRIGHT_E2E_PORT, pass --port, or configure WEBAPP_PORT in ${CONFIG_PATH}."
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --browser)
            BROWSER="$2"
            shift 2
            ;;
        --skip-browser-install)
            SKIP_BROWSER_INSTALL=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            PYTEST_ARGS+=("$@")
            break
            ;;
        *)
            PYTEST_ARGS+=("$1")
            shift
            ;;
    esac
done

cd "$PROJECT_ROOT"

require_command curl

if [[ ! -d ".venv" ]]; then
    print_error "Python virtual environment not found at ${PROJECT_ROOT}/.venv"
    exit 1
fi

source ".venv/bin/activate"

PORT="$(resolve_port)"

if [[ "$SKIP_BROWSER_INSTALL" == "false" ]]; then
    print_info "Installing Playwright browser binaries"
    python -m playwright install "$BROWSER"
fi

export PLAYWRIGHT_BASE_URL="http://${HOST}:${PORT}"
export PLAYWRIGHT_E2E_PORT="$PORT"

print_info "Checking Erie Iron at ${PLAYWRIGHT_BASE_URL}"
require_running_erie_iron "${PLAYWRIGHT_BASE_URL}/health/"

print_info "Running Playwright pytest suite against ${PLAYWRIGHT_BASE_URL}"
pytest \
    -c frontend/tests-end-to-end/pytest.ini \
    frontend/tests-end-to-end/tests \
    --browser "$BROWSER" \
    "${PYTEST_ARGS[@]}"
