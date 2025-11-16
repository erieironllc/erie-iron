#!/bin/bash
set -euo pipefail

# Debug output removed

# Target Account Bootstrap Script
# Sets up cross-account IAM role and permissions for Erie Iron self-driving coder agent
# 
# Options:
#   --reset    Destroy existing OpenTofu infrastructure and recreate (fixes state drift)

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Automatically detect control plane profile from current AWS session
if [[ -n "$AWS_PROFILE" ]]; then
    # Use currently set AWS profile
    CONTROL_PLANE_PROFILE="$AWS_PROFILE"
elif [[ -n "$AWS_DEFAULT_PROFILE" ]]; then
    # Use AWS default profile environment variable
    CONTROL_PLANE_PROFILE="$AWS_DEFAULT_PROFILE"
else
    # Fallback to original default
    CONTROL_PLANE_PROFILE="erieiron"
fi

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

# Parse flags
RESET_FLAG=false
FORCE_CLEAN=false

if [[ $# -ge 1 && "$1" == "--reset" ]]; then
    RESET_FLAG=true
    shift  # Remove --reset from arguments
    
    # Check for additional --force-clean flag
    if [[ $# -ge 1 && "$1" == "--force-clean" ]]; then
        FORCE_CLEAN=true
        shift  # Remove --force-clean from arguments
    fi
fi

# Validate arguments
if [[ $# -ne 2 ]]; then
    print_error "Usage: $0 [--reset [--force-clean]] <profile> <env_type>"
    echo ""
    echo "Options:"
    echo "  --reset          - Destroy existing OpenTofu state and recreate (fixes state conflicts)"
    echo "  --force-clean    - Skip hanging destroy operations and force clean state (use with --reset)"
    echo ""
    echo "Parameters:"
    echo "  profile          - AWS credentials profile for target account authentication"
    echo "  env_type         - Environment type: 'dev' or 'production'"
    echo ""
    echo "Example:"
    echo "  $0 curators-sso dev"
    echo "  $0 --reset curators-sso dev"
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

# Function to reset OpenTofu infrastructure
reset_opentofu_infrastructure() {
    local profile="$1"
    local target_account_id="$2"
    local env_type="$3"
    local business_name="$4"
    
    print_warning "=== RESET MODE ACTIVATED ==="
    print_warning "This will DESTROY and RECREATE all infrastructure in $env_type environment"
    print_warning "Target Account: $target_account_id"
    if [[ "$FORCE_CLEAN" == "true" ]]; then
        print_warning "FORCE CLEAN: Will skip hanging destroy operations and force clean state"
    fi
    echo ""
    
    # Single confirmation for reset (all environments)
    read -p "Are you sure you want to reset? (yes/no): " confirmation
    if [[ "$confirmation" != "yes" ]]; then
        print_error "Reset cancelled by user"
        exit 1
    fi
    
    # Set AWS profile for target account operations
    export AWS_PROFILE="$profile"
    
    # Navigate to target account provisioning directory
    if [[ ! -d "opentofu/target_account_provisioning" ]]; then
        print_error "OpenTofu directory not found: opentofu/target_account_provisioning"
        return 1
    fi
    
    cd opentofu/target_account_provisioning
    
    # Create temporary variables file for destroy operations
    local temp_vars_file="/tmp/reset_vars_${target_account_id}.tfvars"
    cat > "$temp_vars_file" << EOF
target_account_id = "$target_account_id"
business_name = "$(echo "$business_name" | tr '[:upper:]' '[:lower:]')"
env_type = "$env_type"
external_id = "dummy-external-id-for-destroy"
control_plane_account_id = "782005355493"
vpc_cidr = "10.90.0.0/16"
public_subnet_cidrs = ["10.90.0.0/20", "10.90.16.0/20"]
private_subnet_cidrs = ["10.90.32.0/20", "10.90.48.0/20"]
enable_nat_gateway = true
single_nat_gateway = true
enable_vpc_endpoints = true
cost_optimized_for_dev = true
EOF
    
    if [[ "$FORCE_CLEAN" == "true" ]]; then
        print_info "Step 1: Skipping destroy (force-clean mode) - cleaning state directly..."
        
        # Skip the hanging destroy and go straight to state cleanup
        print_info "Cleaning remote state to force fresh deployment..."
        local state_bucket="erieiron-opentofu-state-${business_name,,}-${target_account_id}"
        local state_key="${business_name,,}/${target_account_id}/target-account-bootstrap/terraform.tfstate"
        
        if aws s3 rm "s3://$state_bucket/$state_key" >/dev/null 2>&1; then
            print_success "Removed remote state file"
        else
            print_info "Remote state file cleanup skipped (may not exist)"
        fi
        
        # Clean DynamoDB lock table entirely for force-clean
        print_info "Cleaning DynamoDB lock table entirely (force-clean mode)..."
        if aws dynamodb delete-table --table-name opentofu-locks >/dev/null 2>&1; then
            print_success "Deleted DynamoDB lock table"
            print_info "Waiting for table deletion to complete..."
            # Wait for the table to be fully deleted before proceeding
            for i in {1..30}; do
                if ! aws dynamodb describe-table --table-name opentofu-locks >/dev/null 2>&1; then
                    print_success "DynamoDB table deletion confirmed"
                    break
                fi
                if [ $i -eq 30 ]; then
                    print_warning "Table deletion taking longer than expected, continuing..."
                    break
                fi
                sleep 2
            done
        else
            print_info "DynamoDB lock table cleanup skipped (may not exist)"
        fi
        
        # Clean up all VPC infrastructure from previous runs
        print_info "Cleaning up conflicting VPC infrastructure (force-clean mode)..."
        
        # Get all ErieIron-managed VPCs
        local vpc_ids=$(aws ec2 describe-vpcs --filters "Name=tag:ManagedBy,Values=ErieIron" --query 'Vpcs[].VpcId' --output text)
        
        for vpc_id in $vpc_ids; do
            if [[ -n "$vpc_id" && "$vpc_id" != "None" ]]; then
                print_info "Cleaning up VPC: $vpc_id"
                
                # Clean up NAT gateways first
                aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$vpc_id" --query 'NatGateways[?State==`available`].NatGatewayId' --output text | tr '\t' '\n' | while read -r nat_id; do
                    if [[ -n "$nat_id" && "$nat_id" != "None" ]]; then
                        print_info "Deleting NAT Gateway: $nat_id"
                        aws ec2 delete-nat-gateway --nat-gateway-id "$nat_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Clean up route table associations (non-main)
                aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc_id" --query 'RouteTables[?Associations[?!Main]].Associations[?!Main].RouteTableAssociationId' --output text | tr '\t' '\n' | while read -r assoc_id; do
                    if [[ -n "$assoc_id" && "$assoc_id" != "None" ]]; then
                        print_info "Disassociating route table: $assoc_id"
                        aws ec2 disassociate-route-table --association-id "$assoc_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Clean up custom route tables
                aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc_id" --query 'RouteTables[?Associations[?!Main]].RouteTableId' --output text | tr '\t' '\n' | while read -r rt_id; do
                    if [[ -n "$rt_id" && "$rt_id" != "None" ]]; then
                        print_info "Deleting route table: $rt_id"
                        aws ec2 delete-route-table --route-table-id "$rt_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Clean up subnets
                aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc_id" --query 'Subnets[].SubnetId' --output text | tr '\t' '\n' | while read -r subnet_id; do
                    if [[ -n "$subnet_id" && "$subnet_id" != "None" ]]; then
                        print_info "Deleting subnet: $subnet_id"
                        aws ec2 delete-subnet --subnet-id "$subnet_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Clean up internet gateways
                aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$vpc_id" --query 'InternetGateways[].InternetGatewayId' --output text | tr '\t' '\n' | while read -r igw_id; do
                    if [[ -n "$igw_id" && "$igw_id" != "None" ]]; then
                        print_info "Detaching and deleting IGW: $igw_id"
                        aws ec2 detach-internet-gateway --internet-gateway-id "$igw_id" --vpc-id "$vpc_id" >/dev/null 2>&1 || true
                        aws ec2 delete-internet-gateway --internet-gateway-id "$igw_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Clean up security groups (except default)
                aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$vpc_id" --query 'SecurityGroups[?GroupName!=`default`].GroupId' --output text | tr '\t' '\n' | while read -r sg_id; do
                    if [[ -n "$sg_id" && "$sg_id" != "None" ]]; then
                        print_info "Deleting security group: $sg_id"
                        aws ec2 delete-security-group --group-id "$sg_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Clean up VPC endpoints
                aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpc_id" --query 'VpcEndpoints[].VpcEndpointId' --output text | tr '\t' '\n' | while read -r vpe_id; do
                    if [[ -n "$vpe_id" && "$vpe_id" != "None" ]]; then
                        print_info "Deleting VPC endpoint: $vpe_id"
                        aws ec2 delete-vpc-endpoint --vpc-endpoint-id "$vpe_id" >/dev/null 2>&1 || true
                    fi
                done
                
                # Finally delete the VPC
                print_info "Deleting VPC: $vpc_id"
                aws ec2 delete-vpc --vpc-id "$vpc_id" >/dev/null 2>&1 || true
            fi
        done
        
        # Wait a moment for deletions to propagate
        print_info "Waiting 30 seconds for VPC cleanup to propagate..."
        sleep 30
        
        # Clean up IAM role for complete fresh start
        print_info "Cleaning up existing IAM role (force-clean mode)..."
        if aws iam get-role --role-name ErieIronTargetAccountAgentRole >/dev/null 2>&1; then
            # Remove role policies first
            aws iam list-role-policies --role-name ErieIronTargetAccountAgentRole --query 'PolicyNames' --output text | tr '\t' '\n' | while read -r policy_name; do
                if [[ -n "$policy_name" && "$policy_name" != "None" ]]; then
                    print_info "Deleting role policy: $policy_name"
                    aws iam delete-role-policy --role-name ErieIronTargetAccountAgentRole --policy-name "$policy_name" >/dev/null 2>&1 || true
                fi
            done
            
            # Remove attached managed policies
            aws iam list-attached-role-policies --role-name ErieIronTargetAccountAgentRole --query 'AttachedPolicies[].PolicyArn' --output text | tr '\t' '\n' | while read -r policy_arn; do
                if [[ -n "$policy_arn" && "$policy_arn" != "None" ]]; then
                    print_info "Detaching managed policy: $policy_arn"
                    aws iam detach-role-policy --role-name ErieIronTargetAccountAgentRole --policy-arn "$policy_arn" >/dev/null 2>&1 || true
                fi
            done
            
            print_info "Deleting IAM role: ErieIronTargetAccountAgentRole"
            aws iam delete-role --role-name ErieIronTargetAccountAgentRole >/dev/null 2>&1 || true
            print_success "IAM role cleaned up"
        else
            print_info "IAM role already doesn't exist"
        fi
        
        # Remove local state files
        rm -f terraform.tfstate terraform.tfstate.backup .terraform.lock.hcl
        rm -rf .terraform/
        
        print_success "Force clean completed - will create fresh infrastructure"
        
    else
        print_info "Step 1: Attempting to destroy existing infrastructure..."
        
        # Try to destroy with current state first - but with timeout
        if timeout 120s tofu destroy -auto-approve -var-file="$temp_vars_file" 2>/dev/null; then
            print_success "Infrastructure destroyed successfully"
        else
        print_warning "Normal destroy failed or timed out (likely due to hanging subnet deletion)"
        print_info "Step 2: Attempting destroy with disabled locking and timeout..."
        
        # Try destroy without locking in case DynamoDB lock table is missing - with timeout  
        if timeout 120s tofu destroy -auto-approve -lock=false -var-file="$temp_vars_file" 2>/dev/null; then
            print_success "Infrastructure destroyed successfully (with disabled locking)"
        else
            print_warning "Destroy with disabled locking also failed or timed out"
            print_info "Step 3: Manual cleanup of hanging resources..."
            
            # Manual cleanup of resources that commonly hang
            print_info "Attempting manual cleanup of network resources..."
            
            # Find and clean up network interfaces that might be blocking subnet deletion
            local vpc_id=$(aws ec2 describe-vpcs --filters "Name=tag:ManagedBy,Values=ErieIron" --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "")
            
            if [[ -n "$vpc_id" && "$vpc_id" != "None" && "$vpc_id" != "null" ]]; then
                print_info "Found VPC: $vpc_id - cleaning up resources..."
                
                # Clean up NAT gateways first (they block subnet deletion)
                aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$vpc_id" --query 'NatGateways[?State==`available`].NatGatewayId' --output text 2>/dev/null | tr '\t' '\n' | while read -r nat_id; do
                    if [[ -n "$nat_id" && "$nat_id" != "None" ]]; then
                        print_info "Deleting NAT Gateway: $nat_id"
                        aws ec2 delete-nat-gateway --nat-gateway-id "$nat_id" 2>/dev/null || true
                    fi
                done
                
                # Wait a moment for NAT gateway deletion to propagate
                print_info "Waiting 30 seconds for NAT gateway deletion..."
                sleep 30
                
                # Now try subnet deletion again
                aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc_id" --query 'Subnets[].SubnetId' --output text 2>/dev/null | tr '\t' '\n' | while read -r subnet_id; do
                    if [[ -n "$subnet_id" && "$subnet_id" != "None" ]]; then
                        print_info "Attempting to delete subnet: $subnet_id"
                        aws ec2 delete-subnet --subnet-id "$subnet_id" 2>/dev/null || true
                    fi
                done
            fi
            
            print_info "Step 4: Force state cleanup (bypass hanging resources)..."
            
            # Backup existing state files
            if [[ -f terraform.tfstate ]]; then
                cp terraform.tfstate "terraform.tfstate.backup.$(date +%Y%m%d_%H%M%S)"
                print_info "Backed up existing state file"
            fi
            
            # Clean remote state (S3) to force fresh start
            print_info "Cleaning remote state to force fresh deployment..."
            local state_bucket="erieiron-opentofu-state-${business_name,,}-${target_account_id}"
            local state_key="${business_name,,}/${target_account_id}/target-account-bootstrap/terraform.tfstate"
            
            if aws s3 rm "s3://$state_bucket/$state_key" 2>/dev/null; then
                print_success "Removed remote state file"
            else
                print_info "Remote state file cleanup skipped (may not exist)"
            fi
            
            # Remove local problematic state files
            rm -f terraform.tfstate terraform.tfstate.backup .terraform.lock.hcl
            rm -rf .terraform/
            
            print_warning "State cleaned - bootstrap will create fresh infrastructure"
            print_warning "Note: Some AWS resources may still exist and will need manual cleanup later"
            
            print_info "Step 4: Attempting manual resource cleanup..."
            
            # Try to remove specific problematic resources directly
            print_info "Cleaning up route table associations..."
            
            # Find VPC by business name tag (use business name from bootstrap script context)
            # Note: This runs in opentofu/target_account_provisioning directory, so we need to get business name from parent context
            local vpc_id=$(aws ec2 describe-vpcs --filters "Name=tag:ManagedBy,Values=ErieIron" --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "")
            
            if [[ -n "$vpc_id" && "$vpc_id" != "None" && "$vpc_id" != "null" ]]; then
                print_info "Found VPC: $vpc_id"
                aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc_id" --query 'RouteTables[].Associations[?Main==`false`].RouteTableAssociationId' --output text 2>/dev/null | tr '\t' '\n' | while read -r assoc_id; do
                    if [[ -n "$assoc_id" && "$assoc_id" != "None" ]]; then
                        print_info "Cleaning up route table association: $assoc_id"
                        aws ec2 disassociate-route-table --association-id "$assoc_id" 2>/dev/null || true
                    fi
                done
            else
                print_info "No VPC found for cleanup"
            fi
            
            print_warning "If AWS resources still exist, they may need manual cleanup"
            print_warning "Check AWS Console for orphaned VPC resources in account $target_account_id"
        fi
    fi
    fi
    
    # Clean up temporary variables file
    rm -f "$temp_vars_file" 2>/dev/null
    
    # Return to project root
    cd ../../
    
    print_success "Reset preparation complete"
    print_info "Bootstrap will now proceed with clean infrastructure creation"
    echo ""
}

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
# Execute reset if requested
if [[ "$RESET_FLAG" == "true" ]]; then
    reset_opentofu_infrastructure "$PROFILE" "$TARGET_ACCOUNT_ID" "$ENV_TYPE" "$BUSINESS_NAME" || {
        print_error "Reset operation failed"
        exit 1
    }
fi

print_info "=== Phase 1: Target Account IAM Role Creation ==="
print_info "Using target account credentials to create IAM role..."
export AWS_PROFILE="$PROFILE"

# Execute Phase 1: Target account operations (no Django dependencies)
if [[ "$RESET_FLAG" == "true" ]]; then
    PHASE1_OUTPUT=$(./scripts/bootstrap_target_account_phase_1.sh "$TARGET_ACCOUNT_ID" "$SANITIZED_BUSINESS_NAME" "$ENV_TYPE" "$EXTERNAL_ID" "$STATE_BUCKET_NAME" "$STATE_KEY" "--reset") || {
        print_error "Phase 1 (Target Account) bootstrap failed"
        print_error "Check the logs above for detailed error information"
        exit 1
    }
else
    PHASE1_OUTPUT=$(./scripts/bootstrap_target_account_phase_1.sh "$TARGET_ACCOUNT_ID" "$SANITIZED_BUSINESS_NAME" "$ENV_TYPE" "$EXTERNAL_ID" "$STATE_BUCKET_NAME" "$STATE_KEY") || {
        print_error "Phase 1 (Target Account) bootstrap failed"
        print_error "Check the logs above for detailed error information"
        exit 1
    }
fi

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
