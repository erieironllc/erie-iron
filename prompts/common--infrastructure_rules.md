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
- The Dockerfile **must always** extend this base image: "782005355493.dkr.ecr.us-west-2.amazonaws.com/base-images:python-3.11-slim"
- You can safely ignore this warning:  "WARNING: The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8)"
- If Lambda code requires `AWS_DEFAULT_REGION` or `AWS_REGION`, the CloudFormation configuration must pass these in from the `${AWS::Region}` variable.

### CloudFormation File Enforcement
- All infrastructure definitions must go in `infrastructure.yaml` only.
- Inline IAM policy attachments (e.g., `AWS::IAM::Policy` with `Roles: [!Ref TaskRoleArn]`) belong in `infrastructure.yaml`; runtime code must not create or modify IAM.
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

2. **Single-role assumption**  
   The CloudFormation stack must use the same IAM role for all resources and actions.  
   Do **not** create or reference multiple roles within the stack unless explicitly instructed otherwise.  
   This aligns with Erie Iron’s architecture and simplifies role management.

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
```
  StackIdentifier:
    Type: String
    AllowedPattern: '^[a-z0-9-]{1,40}$'
    ConstraintDescription: 'Lowercase letters, numbers, and dashes only; max 40 chars.'
    Description: Combined project name and environment identifier (e.g., "project-env")
  TaskRoleArn:
    Type: String
    Description: "Required: IAM Role ARN to be used by this stack for ECS tasks, Lambda, and other services. The stack will not create service-specific roles; provide a full role ARN (e.g., arn:aws:iam::123456789012:role/MyTaskRole)."
    ConstraintDescription: "Must be a valid IAM Role ARN (not a role name) and assumable by your CI/CD principal; the role's trust policy should include lambda.amazonaws.com and/or ecs-tasks.amazonaws.com as applicable. Provide an ARN (not a short name)."
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
- **Never** add `AWS::IAM::Role`. Inline `AWS::IAM::Policy` resources are allowed only when `Roles: [!Ref TaskRoleArn]`, the statements are least privilege, and justification comments explain each permission.
- **Never** generate or plan direct interactions with AWS services via the `boto3` client for infrastructure management.
- **Never** define a Lambda function with an environment variable name beginning with `AWS_`.  
    - These prefixes are reserved by AWS and will cause the CloudFormation deployment to fail.
- **Never** introduce a new CloudFormation parameter without a default value.  
    - If a new parameter is needed, you **must** supply a default.  
    - If no suitable default can be provided, you must raise `agent blocked` instead of generating the parameter.  
- **Never** hardcode resource names (like S3 bucket names, SQS queue names, etc. - this applies to **any and all** named aws service or resources - the only exception is RDS DBName, which is always `appdb`)  
    - all resource names **must** be namespaced with the StackIdentifier - eg `!Sub "${StackIdentifier}-<resource_name>"`
    - if you discover hardcoded resource names in the infrastructure.yaml, you **must** fix them by namespacing them with `!Sub "${StackIdentifier}-<resource_name>"`
