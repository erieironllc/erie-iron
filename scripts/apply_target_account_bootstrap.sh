#!/bin/bash
set -euo pipefail

# Target Account Bootstrap Script
# Sets up cross-account IAM role and permissions for Erie Iron self-driving coder agent

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Validate arguments
if [[ $# -ne 3 ]]; then
    print_error "Usage: $0 <target_account_id> <business_name> <env_type>"
    echo ""
    echo "Parameters:"
    echo "  target_account_id - AWS account ID (12 digits) where infrastructure will be deployed"
    echo "  business_name     - Business identifier in Erie Iron system"
    echo "  env_type          - Environment type: 'dev' or 'production'"
    echo ""
    echo "Example:"
    echo "  $0 123456789012 example-business dev"
    exit 1
fi

TARGET_ACCOUNT_ID="$1"
BUSINESS_NAME="$2"
ENV_TYPE="$3"

# Validate environment type
if [[ ! "$ENV_TYPE" =~ ^(dev|production)$ ]]; then
    print_error "env_type must be 'dev' or 'production', got: $ENV_TYPE"
    exit 1
fi

# Validate account ID format
if [[ ! "$TARGET_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
    print_error "target_account_id must be exactly 12 digits, got: $TARGET_ACCOUNT_ID"
    exit 1
fi

print_info "=== Target Account Bootstrap ==="
print_info "Target Account ID: $TARGET_ACCOUNT_ID"
print_info "Business Name: $BUSINESS_NAME"
print_info "Environment Type: $ENV_TYPE"
echo ""

# Check current AWS identity
print_info "Verifying AWS credentials for target account..."
CURRENT_AWS_IDENTITY=$(aws sts get-caller-identity 2>/dev/null || {
    print_error "Failed to get AWS caller identity"
    print_error "Please ensure AWS CLI is configured with credentials for target account: $TARGET_ACCOUNT_ID"
    exit 1
})

CURRENT_ACCOUNT_ID=$(echo "$CURRENT_AWS_IDENTITY" | jq -r '.Account')
print_info "Current AWS identity:"
echo "$CURRENT_AWS_IDENTITY" | jq '.'

# Verify we're in the correct target account
if [[ "$CURRENT_ACCOUNT_ID" != "$TARGET_ACCOUNT_ID" ]]; then
    print_error "AWS credentials are for account $CURRENT_ACCOUNT_ID, but target account is $TARGET_ACCOUNT_ID"
    print_error "Please configure AWS CLI with credentials for the target account"
    exit 1
fi

print_success "AWS credentials verified for target account $TARGET_ACCOUNT_ID"
echo ""

# Confirm execution
print_warning "This script will create an IAM role 'ErieIronTargetAccountAgentRole' in the target account"
print_warning "and store cross-account access credentials in the control plane's Secrets Manager."
echo ""
read -p "Continue with bootstrap? (y/N): " -r
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Bootstrap aborted by user"
    exit 0
fi

echo ""
print_info "Starting bootstrap process..."

# Check if we're in the right directory (should contain manage.py)
if [[ ! -f "manage.py" ]]; then
    print_error "This script must be run from the project root directory (where manage.py is located)"
    exit 1
fi

# Execute bootstrap management command
print_info "Executing bootstrap management command..."
python manage.py bootstrap_target_account "$TARGET_ACCOUNT_ID" "$BUSINESS_NAME" "$ENV_TYPE" || {
    print_error "Bootstrap management command failed"
    print_error "Check the logs for detailed error information"
    exit 1
}

echo ""
print_success "=== Bootstrap Completed Successfully! ==="
print_success "Target account $TARGET_ACCOUNT_ID is now configured for Erie Iron operations"
echo ""
print_info "Next steps:"
print_info "1. CloudAccount record has been created in the database"
print_info "2. Cross-account credentials are stored in Secrets Manager"
print_info "3. The self-driving coder agent can now deploy infrastructure to this account"
print_info "4. You can assign this cloud account to infrastructure stacks as needed"
echo ""
print_info "To verify the setup, check the CloudAccount record in Django admin or database."