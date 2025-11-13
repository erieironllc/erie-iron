# Curators Target Account Deployment Guide

## Overview

This document provides step-by-step instructions for setting up the "curators" AWS target account (659418876324) to receive deployments from the Erie Iron self-driving coder agent. This is a one-time setup process that must be completed before running `self_driving_coder_agent_tofu.py`.

## Background: Why Target Account Bootstrap is Required

AWS accounts are completely isolated security boundaries. By default, one AWS account cannot perform operations in another account. To enable Erie Iron (running in your control plane account) to deploy infrastructure into the "curators" account, you need to create a secure "bridge":

1. **IAM Role as Bridge**: Create an IAM role in the target account that your control plane can "assume"
2. **Trust Relationship**: Configure the role to trust only your specific control plane account
3. **Security Token**: Use an external ID as an additional security layer (like a shared password)

## Prerequisites

Before starting, ensure you have:

- [ ] Admin access to AWS account 659418876324 ("curators" account)
- [ ] The "curators" business record exists in your Django database
- [ ] AWS CLI installed and configured on your machine
- [ ] `jq` command-line tool installed (for JSON parsing)

## Manual Deployment Steps

### Step 1: Configure AWS SSO Profile for Target Account

**Important**: The bootstrap script uses AWS SSO for secure authentication to the TARGET account (659418876324).

```bash
# Configure AWS SSO profile (one-time setup)
aws configure sso --profile curators-sso

# Follow the prompts:
# SSO start URL: [your organization's SSO URL]
# SSO region: [region where your SSO is configured]
# CLI default client Region: us-east-1
# CLI default output format: json
# Select the curators account (659418876324) when prompted
# Choose an appropriate role (e.g., AdministratorAccess)
```

**Configuration creates entry in ~/.aws/config**:
```ini
[profile curators-sso]
sso_start_url = https://your-org.awsapps.com/start
sso_region = us-east-1
sso_account_id = 659418876324
sso_role_name = AdministratorAccess
region = us-east-1
output = json
```

### Step 2: Navigate to Project Root

Ensure you're in the Erie Iron project directory:

```bash
cd /path/to/erieiron2
# Verify you're in the right directory
ls manage.py  # Should exist
```

### Step 3: Run Bootstrap Script

Execute the target account bootstrap script with your SSO profile:

```bash
./scripts/apply_target_account_bootstrap.sh curators-sso dev
```

**What this script does (Two-Phase Process)**:

The bootstrap process uses a **two-phase approach** to solve credential separation issues and avoid cross-account database access problems.

**Initial Setup**:
- Performs `aws sso login --profile curators-sso` to authenticate
- Automatically infers target account ID (659418876324) from profile using `aws sts get-caller-identity`
- Automatically infers business name "curators" from AWS Organizations API (using account name "Curators") 
- Falls back to profile name parsing if Organizations API unavailable
- Generates secure external ID for role assumption security
- Validates account ID format and authentication

#### **Phase 1: Target Account IAM Role Creation**
*Uses target account credentials (curators-sso profile)*

**Script**: `./scripts/bootstrap_phase1_target_account.sh`

**Operations in Target Account (659418876324)**:
- Creates S3 bucket: `erieiron-opentofu-state-curators-659418876324` for OpenTofu state storage
- Configures S3 bucket with versioning, encryption (AES256), and security features
- Creates IAM role: `ErieIronTargetAccountAgentRole`
- Attaches comprehensive permission policy for all agent operations including S3 state access
- Creates DynamoDB table: `opentofu-locks` for OpenTofu state locking
- Configures trust policy to allow your control plane account
- Sets external ID for secure role assumption
- Tags all resources for identification and cost tracking
- Outputs role ARN and external ID to temporary file
- Verifies IAM role creation

**Key Benefits**:
- No Django dependencies = No database credential conflicts
- Direct infrastructure creation using appropriate target account credentials
- Isolated IAM role creation process
- Self-contained OpenTofu state management infrastructure
- Solves "chicken-and-egg" problem by creating S3 bucket before OpenTofu initialization

#### **Phase 2: Control Plane Integration**
*Uses control plane credentials (automatically detected profile)*

**Script**: `./scripts/bootstrap_phase2_control_plane.sh`

**Control Plane Profile Auto-Detection**:
- Script automatically detects available control plane profile:
  - First choice: `erieiron-control`
  - Second choice: `erieiron` 
  - Fallback: `default`
- Switches AWS_PROFILE to control plane credentials

**Operations in Control Plane Account**:
- Reads role information from Phase 1 output file
- Tests cross-account role assumption to validate setup
- Stores role credentials in AWS Secrets Manager 
- Creates CloudAccount database record using `LOCAL_DB_NAME` to prevent AWS credential conflicts
- Links the curators business to the target account

**Automated Verification**:
- Validates control plane credentials before operations
- Confirms cross-account role assumption works correctly
- Verifies CloudAccount database record creation
- Final validation that IAM role exists in target account

**Automated Configuration**:
- Sets cloud account as default for specified environment (dev)
- Enables automatic account selection for future deployments

**Credential Security**:
- Uses appropriate credentials for each phase
- Prevents AWS Secrets Manager access issues during Django initialization
- Clean temporary file cleanup between phases

### Step 4: Bootstrap Completion ✅ (Fully Automated)

The bootstrap script automatically performs all verification and configuration:

✅ **IAM Role Verification**: Confirms `ErieIronTargetAccountAgentRole` exists in target account  
✅ **Database Record Validation**: Ensures CloudAccount record was created successfully  
✅ **Default Account Setup**: Configures cloud account as default for the environment type  
✅ **Auto-Selection Enabled**: Self-driving coder will automatically use this account  

**No manual verification steps required** - the script handles everything and provides clear success/failure feedback.

### Step 5: Infrastructure Linking ✅ (Fully Automated)

The bootstrap script automatically configures the cloud account as the default for the specified environment type. This means:

- All future deployments for "curators" (dev environment) will automatically use account 659418876324
- No manual database configuration needed
- Self-driving coder will automatically resolve to use this account

**Manual override options** (if needed):
```python
# Optional: Manually link specific stacks (rarely needed)
from erieiron_autonomous_agent.models import InfrastructureStack
foundation_stack = InfrastructureStack.objects.get(...)
foundation_stack.cloud_account = cloud_account
foundation_stack.save()
```

## Answering the Key Question: AWS CLI Credentials

**Q: Do I need to execute apply_target_account_bootstrap.sh while logged into the AWS CLI with the curators credentials?**

**A: YES, and it's automated.** The bootstrap script automatically handles AWS SSO login for the TARGET account (659418876324) using the profile you specify.

**How it works:**
- You provide the AWS SSO profile name as the first parameter  
- The script runs `aws sso login --profile <profile>` automatically
- The script infers the target account ID (659418876324) from your authenticated profile
- The script queries AWS Organizations API to get the official account name ("Curators" → "curators")
- If Organizations API is unavailable, falls back to profile name parsing (removes "-sso" suffix)
- All subsequent AWS operations use the specified profile for the detected account

**Benefits of this approach:**
- **Secure**: Uses AWS SSO instead of long-lived access keys
- **Automated**: No manual login steps, business name specification, or account ID specification - the script handles everything
- **Error-Proof**: Eliminates business name and account ID typos by using AWS's authoritative data sources
- **Validated**: Confirms authentication and account format before proceeding
- **Isolated**: Profile-based authentication doesn't affect other AWS CLI usage

**Security Note**: The script only uses the profile for target account operations. Your other AWS profiles remain unaffected.

## Post-Bootstrap Operation

After completing these steps, the self-driving coder agent will automatically:

1. **Detect Target Account**: Resolve that deployments for "curators" should go to account 659418876324
2. **Assume Cross-Account Role**: Use the stored credentials to assume `ErieIronTargetAccountAgentRole`
3. **Deploy Infrastructure**: Execute all infrastructure operations in the target account context

## Troubleshooting

### Common Issues

**Phase 1 Issues:**

**"Failed to get AWS caller identity"**
- Solution: Configure AWS CLI with valid credentials for account 659418876324
- Verify: `aws sts get-caller-identity --profile curators-sso`

**"AWS credentials are for account X, but target account is 659418876324"**
- Solution: Switch to the correct AWS profile or reconfigure AWS CLI
- Check: Ensure AWS_PROFILE is set correctly for Phase 1

**"Permission denied errors during bootstrap"**
- Solution: Ensure your IAM user in account 659418876324 has admin permissions
- Verify: Role creation requires IAM permissions in target account

**Phase 2 Issues:**

**"No suitable control plane AWS profile found"**
- Solution: Configure a control plane profile named `erieiron-control`, `erieiron`, or ensure `default` profile has control plane access
- Check: `aws configure list-profiles` to see available profiles

**"Current AWS credentials are for account X, but control plane account is Y"**
- Solution: The script failed to switch to control plane credentials properly
- Manual fix: Set `export AWS_PROFILE=your-control-plane-profile` before Phase 2

**"Business 'curators' not found in database"**
- Solution: Create the business record in Django admin or shell first
- Note: This error occurs in Phase 2 when accessing Django database

**Django Database Connection Issues:**
- Error: Similar to the original cross-account credential problem
- Solution: The script uses `LOCAL_DB_NAME=erieiron_dev` to avoid this
- Verify: Local database exists with `createdb erieiron_dev`

**General Two-Phase Issues:**

**"Phase 1 output file not found"**
- Issue: Phase 1 failed to complete successfully
- Solution: Check Phase 1 logs for OpenTofu deployment errors
- Debug: Manually run `./scripts/bootstrap_phase1_target_account.sh`

**"Failed to assume target account role"**
- Issue: Cross-account access test fails in Phase 2
- Solution: Verify IAM role trust policy and external ID configuration
- Check: Role exists with `aws iam get-role --role-name ErieIronTargetAccountAgentRole --profile curators-sso`

### Verification Commands

```bash
# Check current AWS identity
aws sts get-caller-identity

# Check if IAM role exists
aws iam get-role --role-name ErieIronTargetAccountAgentRole

# List CloudFormation stacks (if any were created)
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE
```

## Next Steps

Once bootstrap is complete:

1. The curators target account is ready to receive deployments
2. Run `self_driving_coder_agent_tofu.py` as normal
3. Infrastructure will automatically deploy to account 659418876324
4. Monitor deployment logs for successful cross-account operations

## Security Considerations

- **External ID**: Acts as an additional security layer beyond account trust
- **Least Privilege**: IAM role has only necessary permissions for agent operations
- **Temporary Credentials**: Cross-account access uses temporary credentials that expire
- **Audit Trail**: All operations are logged in both accounts for compliance

## OpenTofu State Management Architecture

The bootstrap process creates a comprehensive state management infrastructure in the target account:

### S3 State Storage
- **Bucket Name**: `erieiron-opentofu-state-curators-659418876324`
- **Versioning**: Enabled for state history and rollback capability
- **Encryption**: Server-side encryption (AES256) for state security
- **Access Control**: IAM permissions configured for cross-account access

### DynamoDB State Locking
- **Table Name**: `opentofu-locks`
- **Purpose**: Prevents concurrent OpenTofu operations
- **Billing**: Pay-per-request pricing model

### State Key Pattern
```
curators/659418876324/{stack_namespace_token}/stack.tfstate
```

### Derivable Bucket Names
The system uses a consistent, derivable naming pattern that allows all OpenTofu operations to automatically determine the correct state storage location based on business name and account ID. This eliminates the need for manual configuration while ensuring state isolation between different businesses and accounts.

**Business Name Sanitization**:
The bootstrap script automatically sanitizes business names to meet S3 bucket naming requirements:
- Converts to lowercase
- Removes invalid characters (keeps only letters, numbers, hyphens)
- Validates minimum length (3 characters)
- Validates total bucket name length (≤63 characters)

This one-time setup enables secure, automated deployments to the curators target account while maintaining AWS security best practices and providing robust state management capabilities.