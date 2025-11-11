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

This phase must be run for each new target AWS account that will host business infrastructure.

### Prerequisites:
- Target AWS account exists and you have admin access
- Account ID is known
- Business record exists in the system

### Manual Steps Required:

1. **Prepare account information**:
   ```bash
   export TARGET_ACCOUNT_ID="123456789012"
   export BUSINESS_NAME="example-business"  
   export ENV_TYPE="dev"  # or "production"
   ```

2. **Run target account bootstrap**:
   ```bash
   ./scripts/apply_target_account_bootstrap.sh $TARGET_ACCOUNT_ID $BUSINESS_NAME $ENV_TYPE
   ```

3. **Create CloudAccount database record** (via Django admin or management command):
   ```python
   CloudAccount.objects.create(
       business=business,
       name=f"{business.name}-{ENV_TYPE}",
       account_identifier=TARGET_ACCOUNT_ID,
       credentials_secret_arn=f"{business.secrets_root}/cloud-accounts/{TARGET_ACCOUNT_ID}",
       is_default_dev=(ENV_TYPE == "dev"),
       is_default_production=(ENV_TYPE == "production")
   )
   ```

### What The Bootstrap Script Does:

**OpenTofu Stack Deployed**: `TARGET_ACCOUNT_BOOTSTRAP` (using `./opentofu/target_account_provisioning/stack.tf`)

**AWS Operations in Target Account**:
- **Creates IAM Role**: `ErieIronTargetAccountAgentRole` with trust to control plane account
- **Attaches Permissions**: Comprehensive policy for all agent operations  
- **Sets External ID**: Unique security token for role assumption
- **Tags Resources**: Consistent tagging for identification and cost tracking

**AWS Operations in Control Plane Account**:
- **Stores Credentials**: Creates Secrets Manager secret with role ARN and external ID
- **Updates Database**: Links CloudAccount record to credential secret

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

4. **Create `./scripts/apply_target_account_bootstrap.sh`**:
   - Deploy target account OpenTofu stack
   - Store generated credentials in control plane Secrets Manager
   - Validate role assumption from control plane


### Testing Checklist:

**Target Account Bootstrap**:
- [ ] IAM role created in target account
- [ ] Trust policy allows control plane account access
- [ ] External ID configured correctly
- [ ] Permissions policy attached
- [ ] Credentials stored in control plane Secrets Manager
- [ ] CloudAccount database record created

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