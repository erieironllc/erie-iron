#!/bin/bash
#
# Bash wrapper for OAuth redirect flow testing.
# This script tests the OAuth redirect flow for Cognito authentication.
#
# Usage:
#   ./scripts/test_oauth_redirect_flow.sh [BASE_URL]
#
# Arguments:
#   BASE_URL - Base URL to test (default: http://localhost:8023)
#
# Examples:
#   ./scripts/test_oauth_redirect_flow.sh
#   ./scripts/test_oauth_redirect_flow.sh http://localhost:8000
#   ./scripts/test_oauth_redirect_flow.sh --verbose http://localhost:8023

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default base URL
BASE_URL="${1:-http://localhost:8023}"

# Check if --verbose flag is provided
VERBOSE=""
if [[ "${1:-}" == "--verbose" ]]; then
    VERBOSE="--verbose"
    BASE_URL="${2:-http://localhost:8023}"
fi

# Ensure Python script exists
PYTHON_SCRIPT="$SCRIPT_DIR/test_oauth_redirect_flow.py"
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    echo "Error: Python test script not found at $PYTHON_SCRIPT"
    exit 1
fi

# Run the Python test
echo "Testing OAuth redirect flow at: $BASE_URL"
echo ""

cd "$PROJECT_ROOT"
python3 "$PYTHON_SCRIPT" --base-url "$BASE_URL" $VERBOSE

exit_code=$?

if [[ $exit_code -eq 0 ]]; then
    echo ""
    echo "✓ OAuth redirect flow test PASSED"
else
    echo ""
    echo "✗ OAuth redirect flow test FAILED"
    echo ""
    echo "Common fixes:"
    echo "  1. Ensure the application is running at $BASE_URL"
    echo "  2. Apply OpenTofu changes to update Cognito callback URLs:"
    echo "     cd opentofu/application && tofu apply"
    echo "  3. Check AWS Cognito console for app client configuration"
fi

exit $exit_code
