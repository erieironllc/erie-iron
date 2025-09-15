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

### CloudFormation File Enforcement
- All infrastructure definitions must go in `infrastructure.yaml` only.
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

### RDS Secret Management Contract
- Use `ManageMasterUserPassword: true` in `AWS::RDS::DBInstance`.
- The DBInstanceIdentifier must be parameterized (e.g. `!Sub "${StackIdentifier}-db"`) to namespace the auto-generated secret.
- CloudFormation must output both `!GetAtt RDSInstance.MasterUserSecret.SecretArn` and `!GetAtt RDSInstance.MasterUserSecret.SecretName`.
- No `Parameters.RdsSecretArn`, no `SecretTargetAttachment`, and no Lambda custom resource are required for this pattern.
    

### CloudFormation Update Efficiency Guidelines
Plans must avoid replacement of stateful resources. If replacement is unavoidable, the plan must:
1) mark will_replace: true for each affected resource,
2) explain the justification in plan.rationale.updates,
3) provide a rollback path.

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

- All resource names (like s3 bucket names, SQS queue names, etc) must be namespaced with the StackIdentifier - eg `!Sub "${StackIdentifier}-<resource_name>"`
    - if you discover resource names in the infrastructure.yaml that are not namespaced, you **must** fix them by namespacing them


--- 
## Additional Forbidden Actions
- **Never** generate or plan direct interactions with AWS services via the `boto3` client for infrastructure management.
- **Never** add a new Parameter to infrastructure.yaml without a default value.  If you add a new parameter to infrastructure.yaml without a default value, deployment will fail
- **Never** hardcode resource names (like s3 bucket names, SQS queue names, etc).  all resource names must be namespaced with the StackIdentifier - eg `!Sub "${StackIdentifier}-<resource_name>"`
    - if you discover resource names in the infrastructure.yaml that are not namespaced, you **must** fix them by namespacing them
