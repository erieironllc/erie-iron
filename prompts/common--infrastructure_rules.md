## Infrastructure-Specific Planning Requirements

- Default the AWS region to us-west-2 unless specifically instructed otherwise
- Provisioning plans must prioritize cost-efficiency and security:
  - When choosing AWS services (e.g., App Runner vs ECS vs Lambda), select the **least expensive** option that satisfies load and runtime needs.
  - When provisioning instance-based services (e.g., RDS, EC2), use the **smallest available instance type** that can fulfill the requirements.
  - For test environments, prefer options like `db.t4g.micro`, `t4g.nano`, or similarly low-cost configurations.
  - Avoid overprovisioning or selecting higher tiers by default.
  - IAM roles must follow the **principle of least privilege**—grant only the permissions required to perform the specific task.
- All other infrastructure changes (e.g., VPC, App Runner, RDS, Cognito) must be defined in `infrastructure.yaml`.
- All infrastructure must be defined in `infrastructure.yaml` to ensure coherent, atomic stack deployment and teardown.
- If deployment or infrastructure provisioning fails, it must be fixed before proposing any other code changes.
- If a parameter becomes required, but its CloudFormation description still includes '(optional)', remove the '(optional)' label to reflect its new required status.
- All resources must specify deletion policies that ensure clean, autonomous stack deletion. Do not use `Retain` policies or any configuration that prevents full stack teardown.
    - **All resources must set both `DeletionPolicy` and `UpdateReplacePolicy` to `!Ref DeletePolicy`.**
    - **For any SSM parameter (`AWS::SSM::Parameter`) that references other resources (such as referencing Lambda ARNs, Role ARNs, Bucket names, etc.), add a `DependsOn` relationship to those referenced resources so SSM parameters are deleted after the resources they reference.**
    - **For Lambda functions (`AWS::Lambda::Function`), add a `DependsOn` to ensure the function is deleted before any IAM roles it uses. This ensures IAM roles are not deleted while the Lambda function still exists.**
    - **S3 buckets must always set `DeletionPolicy: !Ref DeletePolicy` and, if `DeletePolicy` is `Delete`, must be emptied automatically (using a lifecycle rule or a cleanup Lambda) so CloudFormation can delete the bucket without manual intervention.**
    - **For IAM roles, ensure that no inline policies or external policy attachments prevent deletion. All policies must be removed (or detached) before the role can be deleted.**
    - **CloudFormation stacks must be able to delete cleanly in all environments, with no manual resource cleanup or intervention ever required.**
- Per-task stacks default to a **Shared VPC** strategy; only propose a **Unique VPC** when the task explicitly requires owning the network resources.
- All stacks accept a `VpcStrategy` parameter with allowed values `[Shared, Unique]`. Conditions must include `UseSharedVpc` and `UseUniqueVpc` so prompts, plans, and templates consistently branch on the selected strategy.
- When authoring **Shared VPC** stacks (`UseSharedVpc`), treat the VPC as read-only: never create or delete InternetGateways, route tables, or default routes, and always consume the provided subnets and networking resources.
- When authoring **Unique VPC** stacks (`UseUniqueVpc`), InternetGateway, VPCGatewayAttachment, route table, and default route resources are permitted, but they **must** be guarded with `Condition: UseUniqueVpc` and the gateway attachment must specify `DependsOn: [DefaultPublicRoute]` to guarantee safe teardown sequencing.
- Route53 subdomain routing (not separate VPCs) supplies tenant isolation for Shared VPC deployments; do not expect network-level isolation when `VpcStrategy` is `Shared`.
- Teardown guidance must reflect the strategy: Unique VPC workflows delete routes before detaching the IGW, while Shared VPC instructions must never mention IGW detachment or route deletion steps.
- If CloudFormation reports `DELETE_FAILED` while detaching an internet gateway, determine the active strategy: Unique VPC stacks usually need corrected `DependsOn` ordering, whereas Shared VPC stacks should remove any IGW or route resources from the template.
- The Dockerfile **must always** extend this base image: "782005355493.dkr.ecr.us-west-2.amazonaws.com/base-images:python-3.11-slim"
- You can safely ignore this warning:  "WARNING: The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8)"
- If Lambda code requires `AWS_DEFAULT_REGION` or `AWS_REGION`, the CloudFormation configuration must pass these in from the `${AWS::Region}` variable.

### SES
- If `DomainName` is managed in Route53 in the same AWS account, you must create Route53 record sets in `infrastructure.yaml` to publish the SES verification TXT record, DKIM CNAMEs, and MX records. Do not rely on manual DNS steps.
- If `DomainName` is not in Route53, return `blocked` with `category: "infra_boundary"` and instructions to onboard the domain to Route53 instead of scheduling HUMAN_WORK.
- When deleting SES ReceiptRuleSets, you must call `ses:SetActiveReceiptRuleSet` with `"RuleSetName": ""` via a custom resource (e.g., `Custom::ActivateSesRuleSet`) before attempting deletion so CloudFormation can cleanly remove the rule set. Always add a `DependsOn` from the ReceiptRuleSet to the deactivation resource.
- If a ReceiptRuleSet resource is observed in DELETE_FAILED with "Cannot delete active rule set", ensure the stack configuration includes a `Custom::ActivateSesRuleSet` resource that calls `ses:SetActiveReceiptRuleSet` with `"RuleSetName": ""` before deletion, and add `DependsOn` from the ReceiptRuleSet to this deactivation resource.
- Must: Clear SES active rule set before delete.
- Forbidden: Deleting an SES rule set while it is still active.

### CloudFormation File Enforcement
- All infrastructure definitions must go in `infrastructure.yaml` only.
- Inline IAM policy attachments (e.g., `AWS::IAM::Policy` targeting stack-defined roles) belong in `infrastructure.yaml`; runtime code must not create or modify IAM.
- **When attaching IAM policies:**  
    - Prefer `Roles: [!Ref <RoleLogicalId>]` when the role is defined in this template and assigns a concrete `RoleName`.  
    - Use `RoleArns` only when you must reference an external ARN (avoid this path unless explicitly required).  
    - **Mixing `Roles` and `RoleArns` is strictly forbidden** and will cause deployment failures. Always choose the property that matches the value you provide.
- Creating or modifying any other CloudFormation YAML file is a violation.
- If a plan attempts to edit a different file, correct the plan to use `infrastructure.yaml` — do **not** return `blocked`.

- **IAM roles or permissions related Tasks**
    - Follow the principle of least privilege: include only permissions essential to accomplish the task.
    - Identify all required permissions up front to avoid iteration churn due to missing access
- **Database-Related Tasks**
    - Use AWS RDS for PostgreSQL as the database backend in **all environments**, including development and test.
    - use PostgreSQL engine version >= 16.8
    - Do not assume or configure any locally running PostgreSQL service.
    - Source all connection details from environment variables or AWS Secrets Manager.
    - RDS security groups must only allow inbound traffic from the VPC CIDR (e.g., `VpcCidr`) and the `ClientIpForRemoteAccess` parameter so developers can connect from their current public IP without exposing the database to broader internet ranges.

### CloudFormation Update Efficiency Guidelines
Plans must avoid replacement of stateful resources. If replacement is unavoidable, the plan must:
1) mark will_replace: true for each affected resource,
2) explain the justification in plan.rationale.updates,
3) provide a rollback path.
Inline `AWS::IAM::Policy` attachments should be isolated so they do not introduce replacements or `DependsOn` chains to slow resources like RDS.

### RDS Configuration
The RDS cloudformation configuration should always look like this:
```yaml
  RDSInstance:
    Type: AWS::RDS::DBInstance
    DeletionPolicy: !Ref DeletePolicy
    UpdateReplacePolicy: !Ref DeletePolicy
    Properties:
      Engine: postgres
      DBName: appdb
      DBInstanceIdentifier: !Sub "${StackIdentifier}-db"
      DBInstanceClass: !Ref DBInstanceClass
      ManageMasterUserPassword: true
      MasterUsername: postgres
      AllocatedStorage: !Ref DBAllocatedStorage
      StorageType: gp3
      VPCSecurityGroups: !If
        - CreateNewVPC
        - [!Ref RDSSecurityGroup]
        - [!Ref ExistingRDSSecurityGroupId]
      DBSubnetGroupName: !Ref DBSubnetGroup
      BackupRetentionPeriod: 7
      MultiAZ: false
      PubliclyAccessible: true
      StorageEncrypted: true
      Tags:
        - Key: Name
          Value: !Sub ${StackIdentifier}-db-instance
```

### RDS Secret Management Contract
- Use `ManageMasterUserPassword: true` in `AWS::RDS::DBInstance`.
- The DBInstanceIdentifier must be parameterized (e.g. `!Sub "${StackIdentifier}-db"`) to namespace the auto-generated secret.
- CloudFormation must output `!GetAtt RDSInstance.MasterUserSecret.SecretArn` **only**; do not create a `RdsMasterSecretName` (or any secret name) output parameter.
- No `Parameters.RdsSecretArn`, no `SecretTargetAttachment`, and no Lambda custom resource are required for this pattern.
- RDSSecurityGroup ingress rules must include **exactly** two sources: the VPC’s internal CIDR block and `ClientIpForRemoteAccess`. Remove any broader public CIDR ranges or legacy rules that would expose the database beyond those endpoints.

### RDS Secret Attachment Requirements
- Separate DB instance creation from AWS::SecretsManager::SecretTargetAttachment where possible.
- Detect existing attachment and no-op rather than re-attach.
- Avoid DependsOn unless required by a specific readiness condition. Include a readiness comment when DependsOn is used.

When planning or modifying AWS CloudFormation templates:

1. **Single-stack integrity**  
   All resources for a given business/environment must remain in a single stack unless explicitly instructed otherwise.

2. **Stack-managed roles**  
   CloudFormation must create and manage the IAM roles it needs inside this template.  
   - Each role sets `RoleName: !Sub "${StackIdentifier}-..."` (or equivalent) and respects the 64-character limit.  
   - resources should reference roles with `!GetAtt <Role>.Arn`.  
   - Inline policies must remain least privilege with justification comments.

3. **No-replacement preference**  
   For slow-to-create resources (e.g., RDS databases, ALBs, large ECS services), prefer property changes that avoid `Replacement`.  
   Consult AWS’s update behavior for each resource type and select properties that can update `Without interruption` or `Some interruption` instead of replacement.

4. **Parallelism maximization**  
   Remove unnecessary `DependsOn` and avoid creating implicit dependencies between unrelated resources.  
   Structure the template so CloudFormation can update independent resources concurrently.

5. **Change scope minimization**  
   Avoid changes that trigger updates to unchanged resources (including tags or metadata) unless functionally required.  
   This helps keep updates scoped and faster.

6. **Change set review**  
   Plan for the deployment process to use Change Sets to preview the blast radius.  
   Reject or re-plan if a change set shows unexpected replacements or broad tag churn.

7. **Drift-free baseline**  
   Assume the stack is kept drift-free; do not rely on out-of-band modifications.

---

## CloudFormation Resource Durations Input

You may be given a `cloudformation_durations` list: a JSON array of objects, each with:

```json
[
  {
    "logical_id": "string",         // CloudFormation LogicalResourceId
    "resource_type": "string",      // CloudFormation ResourceType
    "seconds": float                // Longest observed time in seconds from IN_PROGRESS to completion
  }
]
```

- These durations are based on recent stack events from the current deployment.
- Higher `seconds` values indicate slower resources that are potential bottlenecks.
- **Do not propose fully replacing or recreating slow resources unless it is absolutely required to fix the diagnosed error.**
- When possible, prefer targeted changes (e.g., parameter updates, policy tweaks, environment variable changes) that allow slow resources to remain in place so deployments can complete quickly.
- If a slow resource must be updated, explain why and ensure the change is minimal in scope to avoid long waits on subsequent deploys.

Always balance correctness with deployment speed—avoid edits that would trigger unnecessary replacement of high-duration resources.

Your goal is to plan precise and deterministic infrastructure provisioning changes to resolve the diagnosed AWS error. This may include creating or updating IAM roles, S3 buckets, Lambda configuration, CloudFormation resources, or Dockerfile environment wiring. You are not planning general application fixes—your scope is limited to infrastructure and provisioning issues only.

---

## CloudFormation yaml Parsing

When parsing any yaml, you **must** use:

```python
from erieiron_public import agent_tools
agent_tools.parse_cloudformation_yaml(Path(<path to yaml>))  # ✅ Correct
```

### Prohibited Alternatives
- Do **not** use `yaml.safe_load`, `yaml.load`, or any PyYAML loader.
- Do **not** attempt to implement a custom parser for CloudFormation YAML.
- The only valid parser is `agent_tools.parse_cloudformation_yaml`.

### Incorrect Example
```python
yaml.safe_load(Path(<path to yaml>).read_text())  # ❌ Forbidden
```

---

## Resource Name Namespacing

- All AWS resource names (S3, SQS, etc.) must be namespaced with the StackIdentifier, for example: `!Sub "${StackIdentifier}-<resource_name>"`.
    - If you discover resource names in the infrastructure.yaml that are not namespaced, you **must** fix them by namespacing them.
    - The only exception is `RDS DBName`, which must always be hardcoded to `appdb`.

---

## Required Parameters
- The following parameters are required in every `infrastructure.yaml` file. 
- This section **must** be written **exactly** as follows with **no modifications**
- IAM roles are created within the stack
```
  StackIdentifier:
    Type: String
    AllowedPattern: '^[a-z0-9-]{1,40}$'
    ConstraintDescription: 'Lowercase letters, numbers, and dashes only; max 40 chars.'
    Description: Combined project name and environment identifier (e.g., "project-env")
  ClientIpForRemoteAccess:
    Type: String
    Description: "Your current public IPv4 address in CIDR format (e.g., 203.0.113.25/32)"
    AllowedPattern: "^([0-9]{1,3}\\.){3}[0-9]{1,3}/32$"
    ConstraintDescription: "Must be a valid IPv4 address in /32 CIDR notation"
  DeletePolicy:
    Type: String
    Description: "Required: Specifies the deletion and replacement policy for resources. For development or non-production, this value will be 'Delete', for production this value will be 'Retain'"
  VpcStrategy:
    Type: String
    AllowedValues: ["Shared", "Unique"]
    Description: "Choose whether this stack uses a Shared VPC (reuse ExistingVpcId) or a Unique VPC per stack (create a new VPC)."
Conditions:
  UseSharedVpc: !Equals [!Ref VpcStrategy, "Shared"]
  UseUniqueVpc: !Equals [!Ref VpcStrategy, "Unique"]
  CreateNewVPC: !Or 
    - !Condition UseUniqueVpc
    - !And [!Condition UseSharedVpc, !Equals [!Ref ExistingVpcId, ""]]
  UseExistingVpc: !And [!Condition UseSharedVpc, !Not [!Equals [!Ref ExistingVpcId, ""]]]
  UseExistingRdsSg: !Not [!Equals [!Ref ExistingRDSSecurityGroupId, ""]]
  CreateVpcEndpointsInNewVPC: !And [!Condition CreateNewVPC, !Equals [!Ref EnableVpcEndpoints, "true"]]
```

### DeletePolicy usage

- The delete policy for all stack resources is passed in as a parameter named DeletePolicy
- Use this value for any resource (ie RDS) that accepts a delete (or replace) policy
- For example, RDS configurations **must** use the policy from the parameter like this:
```yaml
  RDSInstance:
    Type: AWS::RDS::DBInstance
    DeletionPolicy: !Ref DeletePolicy
    UpdateReplacePolicy: !Ref DeletePolicy
```

--- 
## Additional Forbidden Actions
- **Never** create IAM roles whose `RoleName` omits the `!Ref StackIdentifier` prefix or exceeds 64 characters. Inline `AWS::IAM::Policy` resources are allowed only when they target stack-defined roles, use least-privilege statements, and include justification comments.
- **Never** generate or plan direct interactions with AWS services via the `boto3` client for infrastructure management.
- **Never** define a Lambda function with an environment variable name beginning with `AWS_`.  
    - These prefixes are reserved by AWS and will cause the CloudFormation deployment to fail.
- **Never** introduce a new CloudFormation parameter without a default value.  
    - If a new parameter is needed, you **must** supply a default.  
    - If no suitable default can be provided, you must raise `agent blocked` instead of generating the parameter.  
- **Never** hardcode resource names (like S3 bucket names, SQS queue names, etc. - this applies to **any and all** named aws service or resources - the only exception is RDS DBName, which is always `appdb`)  
    - all resource names **must** be namespaced with the StackIdentifier - eg `!Sub "${StackIdentifier}-<resource_name>"`
    - if you discover hardcoded resource names in the infrastructure.yaml, you **must** fix them by namespacing them with `!Sub "${StackIdentifier}-<resource_name>"`
