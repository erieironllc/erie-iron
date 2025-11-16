#!/bin/bash
set -euo pipefail

# Debug output removed

# Target Account Bootstrap Script
# Sets up cross-account IAM role and permissions for Erie Iron self-driving coder agent

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

CONTROL_PLANE_PROFILE="erieiron"

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
if [[ $# -ne 2 ]]; then
    print_error "Usage: $0 <profile> <env_type>"
    echo ""
    echo "Parameters:"
    echo "  profile          - AWS credentials profile for target account authentication"
    echo "  env_type         - Environment type: 'dev' or 'production'"
    echo ""
    echo "Example:"
    echo "  $0 curators-sso dev"
    echo ""
    echo "Note: Business name and target account ID will be automatically inferred from AWS"
    echo "      Business name: Retrieved from AWS Organizations API (fallback: profile name parsing)"
    echo "      Target account: Retrieved from authenticated AWS profile"
    exit 1
fi

PROFILE="$1"
ENV_TYPE="$2"

# Validate environment type
if [[ ! "$ENV_TYPE" =~ ^(dev|production)$ ]]; then
    print_error "env_type must be 'dev' or 'production', got: $ENV_TYPE"
    exit 1
fi

AWS_BIN="/usr/local/bin/aws"

print_info "=== Target Account Bootstrap ==="
print_info "AWS Profile: $PROFILE"
print_info "Environment Type: $ENV_TYPE"
echo ""

# AWS SSO Login for target account
#print_info "Performing AWS SSO login for profile '$PROFILE'..."
#"$AWS_BIN" sso login --profile "$PROFILE" || {
#    print_error "AWS SSO login failed for profile '$PROFILE'"
#    print_error "Please ensure:"
#    print_error "1. The profile '$PROFILE' is configured in ~/.aws/config"
#    print_error "2. The profile has valid SSO configuration"
#    print_error "3. You have network access to AWS SSO"
#    exit 1
#}
#
#print_success "AWS SSO login successful for profile '$PROFILE'"
#echo ""

# Infer target account ID from AWS profile
print_info "Inferring target account ID from AWS profile..."
CURRENT_AWS_IDENTITY=$("$AWS_BIN" sts get-caller-identity --profile "$PROFILE" 2>/dev/null || {
    print_error "Failed to get AWS caller identity using profile '$PROFILE'"
    print_error "Please ensure AWS profile is configured correctly and SSO login was successful"
    exit 1
})

TARGET_ACCOUNT_ID=$(echo "$CURRENT_AWS_IDENTITY" | jq -r '.Account')
print_info "Inferred target account ID: $TARGET_ACCOUNT_ID"
print_info "Current AWS identity (using profile '$PROFILE'):"
echo "$CURRENT_AWS_IDENTITY" | jq '.'

# Validate account ID format
if [[ ! "$TARGET_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
    print_error "Inferred account ID '$TARGET_ACCOUNT_ID' is not a valid 12-digit AWS account ID"
    print_error "Please check your AWS profile configuration"
    exit 1
fi

print_success "AWS credentials verified for target account $TARGET_ACCOUNT_ID"
echo ""

# Infer business name from AWS Organizations API
print_info "Inferring business name from AWS Organizations..."
AWS_ACCOUNT_INFO=$("$AWS_BIN" organizations describe-account --account-id "$TARGET_ACCOUNT_ID" --profile "$PROFILE" 2>/dev/null || echo "")

if [[ -n "$AWS_ACCOUNT_INFO" ]]; then
    # Extract account name from Organizations API and convert to lowercase for database lookup
    AWS_ACCOUNT_NAME=$(echo "$AWS_ACCOUNT_INFO" | jq -r '.Account.Name' 2>/dev/null)
    if [[ -n "$AWS_ACCOUNT_NAME" && "$AWS_ACCOUNT_NAME" != "null" ]]; then
        BUSINESS_NAME=$(echo "$AWS_ACCOUNT_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')
        print_success "Business name inferred from AWS Organizations: '$AWS_ACCOUNT_NAME' → '$BUSINESS_NAME'"
    else
        print_warning "Could not extract account name from Organizations API response"
        AWS_ACCOUNT_INFO=""
    fi
fi

# Fallback to profile name parsing if Organizations API failed
if [[ -z "$AWS_ACCOUNT_INFO" ]]; then
    print_warning "AWS Organizations API unavailable (may lack permissions), falling back to profile name parsing"
    BUSINESS_NAME=$(echo "$PROFILE" | sed 's/-sso$//')
    if [[ "$BUSINESS_NAME" == "$PROFILE" ]]; then
        print_warning "Profile name '$PROFILE' does not follow expected convention '<business_name>-sso'"
        print_warning "Using full profile name as business name"
    fi
    print_info "Business name inferred from profile: '$BUSINESS_NAME'"
fi
echo ""

print_warning "This script will create an IAM role 'ErieIronTargetAccountAgentRole' in the target account"
print_warning "and store cross-account access credentials in the control plane's Secrets Manager."
echo ""
print_info "Starting bootstrap process..."

# Check if we're in the right directory (should contain manage.py)
if [[ ! -f "manage.py" ]]; then
    print_error "This script must be run from the project root directory (where manage.py is located)"
    exit 1
fi

# Generate external ID for role assumption security
print_info "Generating external ID for secure role assumption..."
EXTERNAL_ID=$(python -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32)))")
print_info "External ID: $EXTERNAL_ID"

# Generate OpenTofu state storage configuration
print_info "Generating OpenTofu state storage configuration..."

# Sanitize business name for S3 bucket naming requirements
# S3 bucket names must be lowercase, only letters/numbers/hyphens, start/end with letter/number
SANITIZED_BUSINESS_NAME=$(echo "$BUSINESS_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]//g' | sed 's/^-\+\|-\+$//g' | sed 's/-\+/-/g')

# Validate sanitized name is not empty and meets minimum length
if [[ -z "$SANITIZED_BUSINESS_NAME" || ${#SANITIZED_BUSINESS_NAME} -lt 3 ]]; then
    print_error "Business name '$BUSINESS_NAME' cannot be sanitized to valid S3 bucket format"
    print_error "S3 bucket names require at least 3 alphanumeric characters"
    exit 1
fi

STATE_BUCKET_NAME="erieiron-opentofu-state-${SANITIZED_BUSINESS_NAME}-${TARGET_ACCOUNT_ID}"
STATE_KEY="${SANITIZED_BUSINESS_NAME}/${TARGET_ACCOUNT_ID}/target-account-bootstrap/terraform.tfstate"

# Validate total bucket name length (max 63 characters)
if [[ ${#STATE_BUCKET_NAME} -gt 63 ]]; then
    print_error "Generated bucket name '$STATE_BUCKET_NAME' exceeds 63 character limit (${#STATE_BUCKET_NAME} chars)"
    print_error "Consider using a shorter business name"
    exit 1
fi

print_info "Original business name: $BUSINESS_NAME"
print_info "Sanitized business name: $SANITIZED_BUSINESS_NAME" 
print_info "State bucket: $STATE_BUCKET_NAME (${#STATE_BUCKET_NAME} chars)"
print_info "State key: $STATE_KEY"

echo ""
print_info "=== Phase 1: Target Account IAM Role Creation ==="
print_info "Using target account credentials to create IAM role..."
export AWS_PROFILE="$PROFILE"

# Execute Phase 1: Target account operations (no Django dependencies)
PHASE1_OUTPUT=$(./scripts/bootstrap_target_account_phase_1.sh "$TARGET_ACCOUNT_ID" "$SANITIZED_BUSINESS_NAME" "$ENV_TYPE" "$EXTERNAL_ID" "$STATE_BUCKET_NAME" "$STATE_KEY") || {
    print_error "Phase 1 (Target Account) bootstrap failed"
    print_error "Check the logs above for detailed error information"
    exit 1
}

print_success "Phase 1 completed successfully"

echo ""
print_info "=== Phase 2: Control Plane Integration ==="
print_info "Switching to control plane credentials for database operations..."

# Detect control plane profile - try common names
print_info "Using control plane profile: $CONTROL_PLANE_PROFILE"
export AWS_PROFILE="$CONTROL_PLANE_PROFILE"

# Execute Phase 2: Control plane operations (Django with correct credentials)
print_info "Executing Phase 2 with file: $PHASE1_OUTPUT"
if [[ -f "$PHASE1_OUTPUT" ]]; then
    print_info "Phase 1 output file exists, proceeding with Phase 2..."
    ./scripts/bootstrap_target_account_phase_2.sh "$PHASE1_OUTPUT"
    PHASE2_EXIT_CODE=$?
    print_info "Phase 2 exit code: $PHASE2_EXIT_CODE"
    if [[ $PHASE2_EXIT_CODE -ne 0 ]]; then
        print_error "Phase 2 (Control Plane) bootstrap failed with exit code $PHASE2_EXIT_CODE"
        print_error "Check the logs above for detailed error information"
        exit 1
    fi
else
    print_error "Phase 1 output file not found: $PHASE1_OUTPUT"
    print_error "Phase 1 may have failed or file was cleaned up prematurely"
    exit 1
fi

print_success "Phase 2 completed successfully"

echo ""
print_info "=== Final Verification ==="

# Verify IAM role was created in target account (switch back to target account credentials)
print_info "Checking IAM role in target account..."
export AWS_PROFILE="$PROFILE"
IAM_ROLE_CHECK=$("$AWS_BIN" iam get-role --role-name ErieIronTargetAccountAgentRole 2>/dev/null || echo "FAILED")
if [[ "$IAM_ROLE_CHECK" == "FAILED" ]]; then
    print_error "Failed to find IAM role 'ErieIronTargetAccountAgentRole' in target account"
    print_error "Bootstrap may have failed. Check the logs above for errors."
    exit 1
else
    print_success "IAM role 'ErieIronTargetAccountAgentRole' verified in target account"
fi

# Verify CloudAccount database record was created (switch back to control plane credentials)  
print_info "Checking CloudAccount database record..."
export AWS_PROFILE="$CONTROL_PLANE_PROFILE"
# export LOCAL_DB_NAME="erieiron_dev"  # DISABLED - use main database to find correct CloudAccount

CLOUDACCOUNT_CHECK=$(python manage.py shell -c "
from erieiron_autonomous_agent.models import CloudAccount
try:
    # Look up CloudAccount by target account ID only (same as Phase 2 verification)
    cloud_account = CloudAccount.objects.get(account_identifier='$TARGET_ACCOUNT_ID')
    print(f'SUCCESS: CloudAccount found - ID: {cloud_account.id}, Secret ARN: {cloud_account.credentials_secret_arn}')
except Exception as e:
    print(f'FAILED: {e}')
" 2>/dev/null)

if [[ "$CLOUDACCOUNT_CHECK" == FAILED* ]]; then
    print_error "Failed to find CloudAccount database record"
    print_error "Details: $CLOUDACCOUNT_CHECK"
    exit 1
else
    print_success "CloudAccount database record verified"
    print_info "  $CLOUDACCOUNT_CHECK"
fi

echo ""
print_info "=== Step 6: Linking to Infrastructure Stacks ==="

# Set the cloud account as default for the business/environment
print_info "Setting cloud account as default for business '$BUSINESS_NAME' ($ENV_TYPE environment)..."
DEFAULT_SETUP=$(python manage.py shell -c "
from erieiron_autonomous_agent.models import CloudAccount
try:
    # Look up CloudAccount by target account ID only (consistent with other lookups)
    cloud_account = CloudAccount.objects.get(account_identifier='$TARGET_ACCOUNT_ID')
    
    # Use existing set_default_flags method for proper default management
    if '$ENV_TYPE' == 'dev':
        cloud_account.set_default_flags(dev=True)
    else:
        cloud_account.set_default_flags(production=True)
    
    print(f'SUCCESS: Set as default {\"$ENV_TYPE\"} account for business {cloud_account.business.name}')
except Exception as e:
    print(f'FAILED: {e}')
" 2>/dev/null)

if [[ "$DEFAULT_SETUP" == FAILED* ]]; then
    print_warning "Failed to set cloud account as default"
    print_warning "Details: $DEFAULT_SETUP"
    print_warning "You may need to manually set this in Django admin"
else
    print_success "Cloud account configured as default for $ENV_TYPE environment"
fi

echo ""
print_success "=== Bootstrap Completed Successfully! ==="
print_success "Target account $TARGET_ACCOUNT_ID is now configured for Erie Iron operations"
echo ""
print_success "✅ Verification Complete:"
print_success "  • IAM role 'ErieIronTargetAccountAgentRole' exists in target account"
print_success "  • CloudAccount database record created and linked to business '$BUSINESS_NAME'"
print_success "  • Cloud account set as default for $ENV_TYPE environment"
echo ""
print_info "🚀 Ready for deployment:"
print_info "  • The self-driving coder agent can now deploy infrastructure to this account"
print_info "  • All infrastructure stacks for '$BUSINESS_NAME' ($ENV_TYPE) will automatically use this account"
print_info "  • Cross-account credentials are securely stored and will be used automatically"
echo ""
print_info "To deploy infrastructure, simply run coding_agent.py as normal."
