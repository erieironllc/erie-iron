# Cross-Account AWS Operations - Developer Guide

## Overview

This guide provides developers with practical instructions for implementing and operating the cross-account AWS infrastructure system. The system enables the self-driving coder agent to deploy infrastructure in separate "target" AWS accounts while running from a central "control plane" account.

## Architecture Elements

### Core Components

1. **Control Plane Account**: The Erie Iron AWS account hosting the self-driving coder agent
2. **Target Accounts**: Separate AWS accounts where business infrastructure is deployed
3. **Cross-Account IAM Role**: `ErieIronTargetAccountAgentRole` - the specific IAM role in target accounts that the Erie Iron orchestration code (self_driving_coder_agent_tofu.py) assumes when running operations
4. **Credential Storage**: AWS Secrets Manager storing target account access credentials
5. **OpenTofu Stacks**: Infrastructure as Code templates for provisioning permissions and infrastructure

### Event Flow Phases

## Phase 1: Target Account Bootstrap

This phase must be run for each new target AWS account that will host business infrastructure. The bootstrap process uses a **two-phase approach** to separate credential concerns and avoid cross-account access issues during Django initialization.

### Prerequisites:
- Target AWS account exists and you have admin access
- AWS CLI installed and configured
- `jq` command-line tool installed (for JSON parsing)
- Business record exists in the system

### Manual Steps Required:

1. **Configure AWS SSO profile for target account**:
   ```bash
   # Configure AWS SSO profile (one-time setup)
   aws configure sso --profile target-account-sso
   # Follow prompts to configure SSO settings including:
   # - SSO start URL (your organization's SSO URL)
   # - SSO region (region where your SSO is configured)
   # - CLI default client Region: us-east-1
   # - CLI default output format: json
   # - Select the target account when prompted
   # - Choose an appropriate role (e.g., AdministratorAccess)
   ```

2. **Run target account bootstrap** (with automatic SSO login, account detection, and business name inference):
   ```bash
   ./scripts/apply_target_account_bootstrap.sh $PROFILE $ENV_TYPE
   ```
   
   Example:
   ```bash
   ./scripts/apply_target_account_bootstrap.sh curators-sso dev
   ```
   
   **What this command does**:
   - Performs `aws sso login --profile $PROFILE` to authenticate
   - Automatically infers target account ID from profile using `aws sts get-caller-identity`
   - Automatically infers business name from AWS Organizations API (with fallback to profile name parsing)
   - Generates secure external ID for role assumption security
   - Validates account ID format and authentication
   - Executes two-phase bootstrap process automatically


### What The Bootstrap Script Does (Two-Phase Process):

The bootstrap process uses a **two-phase approach** to solve credential separation issues and avoid cross-account database access problems.

#### **Phase 1: Target Account IAM Role Creation**
*Uses target account credentials (specified SSO profile)*

**Script**: `./scripts/bootstrap_phase1_target_account.sh`
**OpenTofu Stack**: `TARGET_ACCOUNT_BOOTSTRAP` (using `./opentofu/target_account_provisioning/stack.tf`)

**Operations in Target Account**:
- **Creates S3 State Bucket**: Generates bucket with pattern `erieiron-opentofu-state-{business}-{account}`
- **Configures S3 Security**: Enables versioning, encryption (AES256), and server-side encryption
- **Creates IAM Role**: `ErieIronTargetAccountAgentRole` with trust to control plane account
- **Attaches Permissions**: Comprehensive policy for all agent operations including S3 state access and ECR
- **Creates DynamoDB Table**: `opentofu-locks` table for OpenTofu state locking
- **Creates ECR Repository**: Container registry with name derived from business service token
- **Configures Trust Policy**: Allow your control plane account to assume role
- **Sets External ID**: Unique security token for role assumption security
- **Tags Resources**: Consistent tagging for identification and cost tracking
- **Outputs Role Information**: Saves role ARN and external ID to temporary file
- **Verifies IAM Role Creation**: Confirms role exists in target account

**Key Benefits**:
- No Django dependencies = No database credential conflicts
- Direct infrastructure creation using appropriate target account credentials
- Isolated IAM role creation process
- Self-contained state management infrastructure in target account
- Solves "chicken-and-egg" problem by creating S3 bucket before OpenTofu initialization

#### **Phase 2: Control Plane Integration**
*Uses control plane credentials (automatically detected profile)*

**Script**: `./scripts/bootstrap_phase2_control_plane.sh`
**Django Command**: `bootstrap_target_account_phase2`

**Control Plane Profile Auto-Detection**:
- Script automatically detects available control plane profile:
  - First choice: `erieiron-control`
  - Second choice: `erieiron` 
  - Fallback: `default`
- Switches AWS_PROFILE to control plane credentials

**Operations in Control Plane Account**:
- **Reads Role Information**: From Phase 1 output file
- **Tests Cross-Account Role Assumption**: To validate setup
- **Stores Credentials**: Creates Secrets Manager secret with role ARN and external ID
- **Creates CloudAccount Database Record**: Using `LOCAL_DB_NAME` to prevent AWS credential conflicts
- **Links Business**: Associates the business with the target account
- **Sets Default Account**: Configures cloud account as default for specified environment

**Automated Verification**:
- **Validates Control Plane Credentials**: Before operations
- **Confirms Cross-Account Role Assumption**: Works correctly
- **Verifies CloudAccount Database Record**: Creation successful
- **Final Validation**: IAM role exists in target account

**Credential Security**:
- Uses appropriate credentials for each phase
- Prevents AWS Secrets Manager access issues during Django initialization
- Clean temporary file cleanup between phases

### Generated Secret Structure:
```json
{
  "role_arn": "arn:aws:iam::123456789012:role/ErieIronTargetAccountAgentRole",
  "external_id": "secure-random-external-id",
  "session_name": "erieiron-123456789012",
  "session_duration": 3600
}
```

## Phase 2: Self-Driving Agent Regular Operation

Once bootstrap is complete, the self-driving agent automatically uses target accounts for infrastructure deployment.

### Automatic Account Selection:
The agent resolves which AWS account to use with this priority:
1. **Application Stack Account**: If the application infrastructure stack has an assigned cloud account
2. **Foundation Stack Account**: If the foundation infrastructure stack has an assigned cloud account  
3. **Business Default Account**: The business's default account for the environment type (dev/production)
4. **Erie Iron Fallback**: Erie Iron's default account as last resort

### Credential Flow During Deployment:

1. **Account Resolution**: Agent determines target account from infrastructure stack configuration
2. **Secret Retrieval**: Loads role credentials from Secrets Manager using `credentials_secret_arn`
3. **Role Assumption**: Uses AWS STS to assume cross-account role with external ID
4. **Credential Caching**: Stores temporary credentials with automatic refresh before expiration
5. **Infrastructure Operations**: All AWS operations use target account credentials

### Example Deployment Flow:
```python
# 1. Agent starts infrastructure deployment
def deploy_infrastructure(business, initiative):
    
    # 2. Resolve target account
    cloud_account = get_target_cloud_account(business, initiative)
    
    # 3. Assume role if cross-account
    if cloud_account.credentials_secret_arn:
        credentials = assume_target_account_role(cloud_account)
    else:
        credentials = get_control_plane_credentials()
    
    # 4. Deploy using target account credentials
    deploy_opentofu_stack(credentials, ...)
```

### Monitoring and Troubleshooting:

**Log Sources**:
- **ECS Task Logs**: Self-driving agent execution logs in CloudWatch
- **OpenTofu Logs**: Infrastructure deployment logs with detailed AWS operations
- **Database Records**: InfrastructureStack and CloudAccount audit trails

**Common Issues**:
- **Role Assumption Failures**: Check external ID and trust policy configuration
- **Permission Denials**: Verify IAM policy includes required permissions for specific operations
- **Credential Expiration**: Monitor credential refresh timing and error patterns

## Developer Implementation Tasks

### Required File Modifications:

1. **Update `self_driving_coder_agent_tofu.py`**:
   - Modify `build_cloud_credentials()` to detect target accounts
   - Add `assume_target_account_role()` function for cross-account access
   - Implement credential caching with automatic refresh

2. **Create `./opentofu/target_account_provisioning/stack.tf`**:
   - Define IAM role with trust policy to control plane
   - Reference permission template for policy attachment
   - Output role ARN and external ID for secret storage

3. **Update `./opentofu/target_account_provisioning/target_account_agent_permissions.json.tftpl`**:
   - Review and extend existing permissions as needed
   - Ensure all agent operations are covered
   - Apply least-privilege principles

4. **Two-Phase Bootstrap Scripts**:

   **a. `./scripts/apply_target_account_bootstrap.sh`** (Main orchestrator):
   - Generates external ID for secure role assumption
   - Generates OpenTofu state bucket configuration with sanitized business name  
   - Validates S3 bucket name length and format compliance
   - Executes Phase 1 with target account credentials
   - Switches to control plane credentials for Phase 2
   - Automatic control plane profile detection
   - End-to-end verification of both phases

   **b. `./scripts/bootstrap_phase1_target_account.sh`** (Target account operations):
   - Create S3 bucket for OpenTofu state storage with security configurations
   - Deploy target account OpenTofu stack using S3 backend with dynamic configuration
   - Create IAM role with permissions for S3 state bucket access
   - Create DynamoDB table for state locking
   - Output role information to temporary file for Phase 2
   - Validate role creation in target account

   **c. `./scripts/bootstrap_phase2_control_plane.sh`** (Control plane operations):
   - Store generated credentials in control plane Secrets Manager
   - Use `LOCAL_DB_NAME` to avoid AWS database credential conflicts
   - Test cross-account role assumption
   - Create CloudAccount database record with state bucket configuration

5. **Django Management Command `bootstrap_target_account_phase2.py`**:
   - Simplified Phase 2-only command (no OpenTofu deployment)
   - Handles credential storage and CloudAccount creation
   - Input validation for role ARN and external ID
   - Set default cloud account flags based on environment


### Testing Checklist:

**Phase 1: Target Account Bootstrap**:
- [ ] Target account credentials validated before Phase 1
- [ ] IAM role created in target account
- [ ] Trust policy allows control plane account access
- [ ] External ID configured correctly
- [ ] Permissions policy attached
- [ ] Role information output file created successfully
- [ ] Phase 1 completes without Django database access

**Phase 2: Control Plane Integration**:
- [ ] Control plane credentials detected and validated
- [ ] Role information successfully read from Phase 1 output
- [ ] Cross-account role assumption test passes
- [ ] Credentials stored in control plane Secrets Manager
- [ ] CloudAccount database record created using local database
- [ ] Default environment flags set correctly
- [ ] Phase 2 completes without credential conflicts

**Cross-Account Operations**:
- [ ] Role assumption succeeds from control plane
- [ ] AWS operations execute in target account context
- [ ] Infrastructure deployment completes successfully
- [ ] Resources created with correct tags and naming
- [ ] State files stored in control plane S3 bucket

**Error Handling**:
- [ ] Invalid credentials handled gracefully
- [ ] Permission denials logged with context
- [ ] Credential refresh works before expiration
- [ ] Fallback to control plane account when appropriate

### Security Validations:

**Role Configuration**:
- External ID is unique and securely generated
- Trust policy restricts access to control plane account only
- Session duration follows security best practices (≤1 hour)

**Permission Scope**:
- All granted permissions are necessary for agent operations
- Resource-level restrictions applied where possible
- No overly broad wildcard permissions

**Credential Handling**:
- Temporary credentials cached securely in memory
- No credentials logged or persisted to disk
- Automatic cleanup when credentials expire

## Operational Procedures

### Adding New Target Account:
1. Create AWS account through standard process
2. Run `apply_target_account_bootstrap.sh` script
3. Create CloudAccount database record
4. Assign cloud account to appropriate infrastructure stacks
5. Test deployment to verify configuration

### Rotating Credentials:
1. Generate new external ID
2. Update IAM role trust policy in target account
3. Update stored credentials in Secrets Manager
4. Restart agent processes to pick up new credentials

### Removing Target Account:
1. Destroy all infrastructure in target account
2. Delete IAM role and policies
3. Remove credentials from Secrets Manager
4. Delete CloudAccount database record

This implementation provides a secure, scalable foundation for cross-account AWS operations while maintaining operational simplicity and following AWS security best practices.