#!/bin/bash
set -euo pipefail

# Phase 1: Target Account Bootstrap Script
# Creates IAM role in target account using target account credentials
# No Django dependencies to avoid cross-account database access issues
# Supports --reset flag to clean up problematic OpenTofu state
#
# ⚠️  CRITICAL: This script must output ONLY the file path to stdout
# ALL debug/info output MUST use stderr (>&2) or print_* functions
# Violating this will cause Phase 2 to fail with corrupted $PHASE1_OUTPUT

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output to stderr (so it doesn't interfere with stdout file path)
print_info() {
    echo -e "${BLUE}[PHASE1-INFO]${NC} $1" >&2
}

print_success() {
    echo -e "${GREEN}[PHASE1-SUCCESS]${NC} $1" >&2
}

print_warning() {
    echo -e "${YELLOW}[PHASE1-WARNING]${NC} $1" >&2
}

print_error() {
    echo -e "${RED}[PHASE1-ERROR]${NC} $1" >&2
}

# Function to cleanup temporary files
cleanup_temp_files() {
    local target_account_id="$1"
    print_info "Cleaning up temporary files..."
    
    # Remove OpenTofu variables and plan files
    rm -f "/tmp/bootstrap_phase1_${target_account_id}.tfvars" 2>/dev/null
    rm -f "/tmp/bootstrap_phase1_${target_account_id}.tfplan" 2>/dev/null
    rm -f "/tmp/bootstrap_phase1_${target_account_id}_nolock.tfplan" 2>/dev/null
    
    # Note: Log files and output files are preserved for debugging and phase transitions
}

# Parse reset flag if present
RESET_FLAG=false
if [[ $# -eq 7 && "$7" == "--reset" ]]; then
    RESET_FLAG=true
fi

# Validate arguments
if [[ $# -ne 6 && $# -ne 7 ]]; then
    print_error "Usage: $0 <target_account_id> <business_name> <env_type> <external_id> <state_bucket_name> <state_key> [--reset]"
    exit 1
fi

TARGET_ACCOUNT_ID="$1"
BUSINESS_NAME="$2"
ENV_TYPE="$3"
EXTERNAL_ID="$4"
STATE_BUCKET_NAME="$5"
STATE_KEY="$6"

# Setup cleanup trap for unexpected exits
trap 'cleanup_temp_files "$TARGET_ACCOUNT_ID"' EXIT

print_info "=== Phase 1: Target Account IAM Role Creation ==="
print_info "Target Account ID: $TARGET_ACCOUNT_ID"
print_info "Business Name: $BUSINESS_NAME"
print_info "Environment Type: $ENV_TYPE"
print_info "External ID: $EXTERNAL_ID"
print_info "State Bucket: $STATE_BUCKET_NAME"
print_info "State Key: $STATE_KEY"

# Validate we're using target account credentials
CURRENT_ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text 2>/dev/null || echo "UNKNOWN")
if [[ "$CURRENT_ACCOUNT" != "$TARGET_ACCOUNT_ID" ]]; then
    print_error "Current AWS credentials are for account $CURRENT_ACCOUNT, but target account is $TARGET_ACCOUNT_ID"
    print_error "Please ensure AWS_PROFILE is set to the correct target account profile"
    exit 1
fi

print_success "AWS credentials verified for target account $TARGET_ACCOUNT_ID"

# Create S3 bucket for OpenTofu state storage
print_info "Creating S3 bucket for OpenTofu state storage..."
CURRENT_REGION=$(aws configure get region || echo "us-west-2")

# Check if bucket already exists
if aws s3api head-bucket --bucket "$STATE_BUCKET_NAME" >/dev/null 2>&1; then
    print_success "S3 bucket '$STATE_BUCKET_NAME' already exists"
else
    # Create bucket with appropriate configuration for region
    if [[ "$CURRENT_REGION" == "us-east-1" ]]; then
        # us-east-1 doesn't need location constraint
        aws s3api create-bucket --bucket "$STATE_BUCKET_NAME" >/dev/null 2>&1 || {
            print_error "Failed to create S3 bucket '$STATE_BUCKET_NAME'"
            exit 1
        }
    else
        aws s3api create-bucket \
            --bucket "$STATE_BUCKET_NAME" \
            --create-bucket-configuration LocationConstraint="$CURRENT_REGION" >/dev/null 2>&1 || {
            print_error "Failed to create S3 bucket '$STATE_BUCKET_NAME' in region $CURRENT_REGION"
            exit 1
        }
    fi
    
    print_success "Created S3 bucket '$STATE_BUCKET_NAME' in region $CURRENT_REGION"
    
    # Enable versioning on the bucket
    aws s3api put-bucket-versioning \
        --bucket "$STATE_BUCKET_NAME" \
        --versioning-configuration Status=Enabled >/dev/null 2>&1 || {
        print_warning "Failed to enable versioning on bucket '$STATE_BUCKET_NAME'"
    }
    
    # Enable server-side encryption
    aws s3api put-bucket-encryption \
        --bucket "$STATE_BUCKET_NAME" \
        --server-side-encryption-configuration '{
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "AES256"
                },
                "BucketKeyEnabled": true
            }]
        }' >/dev/null 2>&1 || {
        print_warning "Failed to enable encryption on bucket '$STATE_BUCKET_NAME'"
    }
    
    # Block public access
    aws s3api put-public-access-block \
        --bucket "$STATE_BUCKET_NAME" \
        --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null 2>&1 || {
        print_warning "Failed to block public access on bucket '$STATE_BUCKET_NAME'"
    }
    
    print_success "Configured S3 bucket security settings"
fi

# Prepare OpenTofu variables
TOFU_VARS_FILE="/tmp/bootstrap_phase1_${TARGET_ACCOUNT_ID}.tfvars"
cat > "$TOFU_VARS_FILE" << EOF
target_account_id = "$TARGET_ACCOUNT_ID"
business_name = "$BUSINESS_NAME"
env_type = "$ENV_TYPE"
external_id = "$EXTERNAL_ID"
control_plane_account_id = "782005355493"
EOF

print_info "Created OpenTofu variables file: $TOFU_VARS_FILE"

# Run OpenTofu deployment for IAM role creation
print_info "Deploying IAM role via OpenTofu..."
cd opentofu/target_account_provisioning

# Create temporary log file for all OpenTofu output  
TOFU_LOG_FILE="/tmp/bootstrap_phase1_tofu_${TARGET_ACCOUNT_ID}.log"

print_info $(
  aws sts get-caller-identity | jq  -r '.Arn'
)

# Clean up any existing OpenTofu state to avoid migration prompts
print_info "Cleaning up any existing OpenTofu state..."
rm -rf .terraform terraform.tfstate terraform.tfstate.backup .terraform.lock.hcl

# Additional cleanup for reset mode
if [[ "$RESET_FLAG" == "true" ]]; then
    print_warning "Reset mode: Ensuring clean remote state..."
    
    # Try to delete remote state bucket contents (if accessible)
    print_info "Attempting to clean remote state bucket..."
    if aws s3 rm "s3://$STATE_BUCKET_NAME/$STATE_KEY" >/dev/null 2>&1; then
        print_success "Removed remote state file"
    else
        print_info "Remote state file cleanup skipped (may not exist or no permissions)"
    fi
    
    # Clean entire state bucket path for this business/account
    aws s3 rm "s3://$STATE_BUCKET_NAME/$BUSINESS_NAME/$TARGET_ACCOUNT_ID/" --recursive >/dev/null 2>&1 || print_info "Remote state directory cleanup skipped"
fi

# Initialize with dynamic backend configuration - capture ALL output
print_info "Initializing OpenTofu with backend configuration..."
if ! tofu init \
    -backend-config="bucket=$STATE_BUCKET_NAME" \
    -backend-config="key=$STATE_KEY" \
    -backend-config="region=$CURRENT_REGION" \
    -input=false \
    >> "$TOFU_LOG_FILE" 2>&1; then
    print_error "OpenTofu init failed. Check log: $TOFU_LOG_FILE"
    print_warning "This might be due to missing DynamoDB lock table or S3 bucket"
    print_info "Attempting to continue without backend state for reset scenarios..."
    
    # Try to init without backend for reset scenarios
    if [[ "$RESET_FLAG" == "true" ]]; then
        print_info "Reset mode: Attempting local backend initialization..."
        if ! tofu init -input=false >> "$TOFU_LOG_FILE" 2>&1; then
            print_error "Local backend initialization also failed. Check log: $TOFU_LOG_FILE"
            exit 1
        fi
        print_success "Initialized with local backend for reset"
    else
        exit 1
    fi
fi

# Plan - capture ALL output to log file
print_info "Planning OpenTofu deployment..."

# Try plan with locking first
if tofu plan -var-file="$TOFU_VARS_FILE" -out="/tmp/bootstrap_phase1_${TARGET_ACCOUNT_ID}.tfplan" >> "$TOFU_LOG_FILE" 2>&1; then
    print_success "Plan succeeded with locking"
    DISABLE_LOCKING=false
else
    print_warning "OpenTofu plan failed, possibly due to state locking issues"
    
    # Try plan without locking in case DynamoDB table is missing
    print_info "Attempting plan without state locking..."
    if ! tofu plan -var-file="$TOFU_VARS_FILE" -out="/tmp/bootstrap_phase1_${TARGET_ACCOUNT_ID}_nolock.tfplan" -lock=false >> "$TOFU_LOG_FILE" 2>&1; then
        print_error "OpenTofu plan failed even without locking. Check log: $TOFU_LOG_FILE"
        exit 1
    fi
    print_success "Plan succeeded without locking"
    DISABLE_LOCKING=true
fi

# Import existing resources if they exist
print_info "Checking for and importing existing AWS resources..."

# Note: S3 bucket is created directly via AWS CLI and is not managed by OpenTofu
# Only import the DynamoDB table and IAM resources

# Skip import for now - the apply logic will handle existing resources
print_info "Skipping import step as apply will handle existing resources correctly"

# Re-plan after imports to account for existing resources
print_info "Re-planning after imports..."

# Use the same locking setting for re-plan as the initial plan
if [[ "$DISABLE_LOCKING" == "true" ]]; then
    if ! tofu plan -var-file="$TOFU_VARS_FILE" -out="/tmp/bootstrap_phase1_${TARGET_ACCOUNT_ID}_nolock.tfplan" -lock=false >> "$TOFU_LOG_FILE" 2>&1; then
        print_error "OpenTofu re-plan failed after imports (without locking). Check log: $TOFU_LOG_FILE"
        exit 1
    fi
else
    if ! tofu plan -var-file="$TOFU_VARS_FILE" -out="/tmp/bootstrap_phase1_${TARGET_ACCOUNT_ID}.tfplan" >> "$TOFU_LOG_FILE" 2>&1; then
        print_error "OpenTofu re-plan failed after imports. Check log: $TOFU_LOG_FILE"
        exit 1
    fi
fi

# Apply - capture ALL output to log file
print_info "Applying OpenTofu deployment..."

# When locking is disabled, we need to apply without using a plan file
# because plan files embed locking behavior that cannot be overridden
if [[ "$DISABLE_LOCKING" == "true" ]]; then
    if ! tofu apply -var-file="$TOFU_VARS_FILE" -auto-approve -lock=false >> "$TOFU_LOG_FILE" 2>&1; then
        APPLY_FAILED=true
    else
        APPLY_FAILED=false
    fi
else
    if ! tofu apply "/tmp/bootstrap_phase1_${TARGET_ACCOUNT_ID}.tfplan" >> "$TOFU_LOG_FILE" 2>&1; then
        APPLY_FAILED=true
    else
        APPLY_FAILED=false
    fi
fi

if [[ "$APPLY_FAILED" == "true" ]]; then
    # Check if the failure is due to resources already existing
    if grep -q "already exists" "$TOFU_LOG_FILE" || grep -q "ResourceConflictException" "$TOFU_LOG_FILE" || grep -q "EntityAlreadyExistsException" "$TOFU_LOG_FILE" || grep -q "BucketAlreadyExists" "$TOFU_LOG_FILE"; then
        print_warning "OpenTofu apply failed due to existing resources - updating existing role with new external ID"
        print_info "Check log for details: $TOFU_LOG_FILE"
        
        # Update the existing role's trust policy with the new external ID
        print_info "Updating IAM role trust policy with new external ID..."
        print_info "Target Account ID: $TARGET_ACCOUNT_ID"
        print_info "External ID: $EXTERNAL_ID"
        
        TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": [
          "arn:aws:iam::782005355493:role/xxbev-task-execution-role",
          "arn:aws:iam::${TARGET_ACCOUNT_ID}:role/ErieIronTargetAccountAgentRole"
        ]
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "${EXTERNAL_ID}"
        }
      }
    }
  ]
}
EOF
)
        
        print_info "Trust policy being applied:"
        # CRITICAL: Must redirect to stderr (>&2) - stdout is reserved for file path output only
        # Any stdout output here will corrupt $PHASE1_OUTPUT in the calling script
        echo "$TRUST_POLICY" | jq . >&2 || echo "$TRUST_POLICY" >&2
        
        # Update the role's trust policy
        if aws iam update-assume-role-policy \
            --role-name ErieIronTargetAccountAgentRole \
            --policy-document "$TRUST_POLICY" >> "$TOFU_LOG_FILE" 2>&1; then
            print_success "Updated IAM role trust policy with new external ID: $EXTERNAL_ID"
            print_info "Waiting 5 seconds for AWS to propagate role changes..."
            sleep 5
        else
            print_error "Failed to update IAM role trust policy. Check log: $TOFU_LOG_FILE"
            exit 1
        fi
        
        # Update the existing role's inline policy with the latest template
        print_info "Updating IAM role inline policy with latest permissions template..."
        
        # Get the current AWS region and generate the templated policy
        CURRENT_REGION=$(aws configure get region || echo "us-west-2")
        CONTROL_PLANE_ACCOUNT_ID="782005355493"
        
        # Generate the policy document from the template
        # Note: We need to simulate the templatefile() function that Terraform uses
        # Also remove the Description field as it's not allowed in inline policies
        AGENT_POLICY=$(cat target_account_agent_permissions.json.tftpl | \
            sed "s/\${account_id}/$TARGET_ACCOUNT_ID/g" | \
            sed "s/\${region}/$CURRENT_REGION/g" | \
            sed "s/\${control_plane_account_id}/$CONTROL_PLANE_ACCOUNT_ID/g" | \
            sed "s/\${control_plane_region}/$CURRENT_REGION/g" | \
            sed "s|\${state_bucket_arn}|arn:aws:s3:::$STATE_BUCKET_NAME|g" | \
            sed "s|\${state_bucket_objects_arn}|arn:aws:s3:::$STATE_BUCKET_NAME/*|g" | \
            jq 'del(.Description)')
        
        print_info "Agent policy being applied:"
        # CRITICAL: Must redirect to stderr (>&2) - stdout is reserved for file path output only
        echo "$AGENT_POLICY" | jq . >&2 || echo "$AGENT_POLICY" >&2
        
        # Update the role's inline policy (use account-specific policy name)
        POLICY_NAME="ErieIronAgentPermissions-$TARGET_ACCOUNT_ID"
        if aws iam put-role-policy \
            --role-name ErieIronTargetAccountAgentRole \
            --policy-name "$POLICY_NAME" \
            --policy-document "$AGENT_POLICY" >> "$TOFU_LOG_FILE" 2>&1; then
            print_success "Updated IAM role inline policy with latest permissions template"
            print_info "Waiting 5 seconds for AWS to propagate policy changes..."
            sleep 5
        else
            print_error "Failed to update IAM role inline policy. Check log: $TOFU_LOG_FILE"
            exit 1
        fi
    else
        print_error "OpenTofu apply failed with unexpected error. Check log: $TOFU_LOG_FILE"
        exit 1
    fi
fi

# Extract role information from OpenTofu outputs - capture ALL output to log file
print_info "Extracting role information..."
ROLE_ARN=$(tofu output -raw role_arn 2>> "$TOFU_LOG_FILE" || echo "")
ACTUAL_EXTERNAL_ID=$(tofu output -raw external_id 2>> "$TOFU_LOG_FILE" || echo "")
VPC_CONFIG=$(tofu output -json vpc_config 2>> "$TOFU_LOG_FILE" || echo "")

# If outputs failed, try to construct from known values (for existing resources)
if [[ -z "$ROLE_ARN" || -z "$ACTUAL_EXTERNAL_ID" ]]; then
    print_warning "Could not extract role information from OpenTofu outputs"
    print_info "Attempting to construct role ARN from existing IAM role..."
    
    # Try to get the role ARN directly from AWS IAM
    if aws iam get-role --role-name ErieIronTargetAccountAgentRole >> "$TOFU_LOG_FILE" 2>&1; then
        ROLE_ARN="arn:aws:iam::${TARGET_ACCOUNT_ID}:role/ErieIronTargetAccountAgentRole"
        ACTUAL_EXTERNAL_ID="$EXTERNAL_ID"  # Use the provided external ID
        print_success "Constructed role ARN from existing IAM role: $ROLE_ARN"
    else
        print_error "Failed to extract role information and role does not exist. Check log: $TOFU_LOG_FILE"
        exit 1
    fi
fi

if [[ -z "$VPC_CONFIG" || "$VPC_CONFIG" == "null" ]]; then
    print_error "Failed to extract VPC configuration from OpenTofu outputs. Check log: $TOFU_LOG_FILE"
    exit 1
fi

# Validate role creation - capture ALL output to log file
print_info "Verifying IAM role creation..."
if ! aws iam get-role --role-name ErieIronTargetAccountAgentRole >> "$TOFU_LOG_FILE" 2>&1; then
    print_error "Failed to verify IAM role creation. Check log: $TOFU_LOG_FILE"
    exit 1
fi

print_success "IAM role created successfully: $ROLE_ARN"

# Create ECR repository for business container images
print_info "Creating ECR repository for container images..."

# Function to derive service_token from business name (same logic as common.strip_non_alpha)
strip_non_alpha() {
    echo "$1" | sed 's/[^a-zA-Z]//g'
}

# Function to implement sanitize_aws_name logic
sanitize_aws_name() {
    local name="$1"
    local max_length="${2:-128}"
    
    if [[ -z "$name" ]]; then
        echo "t"
        return
    fi
    
    # Replace underscores and spaces with hyphens
    name=$(echo "$name" | sed 's/[_ ]/-/g')
    
    # Remove invalid characters (keep only alphanumeric and hyphens)
    name=$(echo "$name" | sed 's/[^A-Za-z0-9-]/-/g')
    
    # Collapse multiple hyphens
    name=$(echo "$name" | sed 's/-\+/-/g')
    
    # Remove leading and trailing hyphens and convert to lowercase
    name=$(echo "$name" | sed 's/^-\+\|-\+$//g' | tr '[:upper:]' '[:lower:]')
    
    # If empty, use default
    if [[ -z "$name" ]]; then
        name="t"
    fi
    
    # Ensure name starts with a letter
    if ! echo "$name" | grep -q "^[a-z]"; then
        name="t${name}"
    fi
    
    # Truncate to max length
    name="${name:0:$max_length}"
    
    echo "$name"
}

# Derive service_token from business name (remove non-alphabetic chars and lowercase)
SERVICE_TOKEN=$(strip_non_alpha "$BUSINESS_NAME" | tr '[:upper:]' '[:lower:]')

if [[ -z "$SERVICE_TOKEN" ]]; then
    print_warning "Could not derive service_token from business name '$BUSINESS_NAME', using fallback"
    SERVICE_TOKEN="default"
fi

# Generate ECR repository name using same logic as coding_agent_config.py
ECR_REPO_NAME=$(sanitize_aws_name "$SERVICE_TOKEN")

print_info "Derived service_token: $SERVICE_TOKEN"
print_info "ECR repository name: $ECR_REPO_NAME"

# Check if ECR repository already exists
if aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" >> "$TOFU_LOG_FILE" 2>&1; then
    print_success "ECR repository '$ECR_REPO_NAME' already exists"
else
    print_info "Creating ECR repository '$ECR_REPO_NAME'..."
    if aws ecr create-repository --repository-name "$ECR_REPO_NAME" --image-scanning-configuration scanOnPush=true >> "$TOFU_LOG_FILE" 2>&1; then
        print_success "ECR repository '$ECR_REPO_NAME' created successfully"
    else
        print_error "Failed to create ECR repository '$ECR_REPO_NAME'. Check log: $TOFU_LOG_FILE"
        exit 1
    fi
fi

# Output role information for Phase 2
PHASE1_OUTPUT_FILE="/tmp/bootstrap_phase1_output_${TARGET_ACCOUNT_ID}.json"
cat > "$PHASE1_OUTPUT_FILE" << EOF
{
  "target_account_id": "$TARGET_ACCOUNT_ID",
  "business_name": "$BUSINESS_NAME", 
  "env_type": "$ENV_TYPE",
  "role_arn": "$ROLE_ARN",
  "external_id": "$ACTUAL_EXTERNAL_ID",
  "service_token": "$SERVICE_TOKEN",
  "ecr_repository_name": "$ECR_REPO_NAME",
  "vpc_config": $VPC_CONFIG
}
EOF

print_success "Phase 1 completed successfully!"
print_info "Role information saved to: $PHASE1_OUTPUT_FILE"
print_info "Ready for Phase 2 (Control Plane Integration)"

# Cleanup temporary files using standardized function
cleanup_temp_files "$TARGET_ACCOUNT_ID"

# Show log file location for debugging (to stderr)
print_info "OpenTofu logs saved to: $TOFU_LOG_FILE"

# CRITICAL: This script MUST output ONLY the file path to stdout
# ALL other output must go to stderr (>&2) or it will corrupt $PHASE1_OUTPUT
# in the calling script and cause Phase 2 to fail with "file not found"
echo "$PHASE1_OUTPUT_FILE"
