## Infrastructure-Specific Planning Requirements

### Resource Tagging Standard

All taggable AWS resources must use a consistent tagging pattern with centralized base tags:

1. **Define base tags in a `locals` block** at the top of `stack.tf`:
   ```hcl
   locals {
     base_tags = {
       Stack        = var.StackIdentifier
       ManagedBy    = "ErieIron"
       DeletePolicy = var.DeletePolicy
       Environment  = var.Environment
     }
   }
   ```

2. **For resources requiring only standard tags**, use the base tags directly:
   ```hcl
   resource "aws_security_group" "example" {
     # ... resource configuration ...
     tags = local.base_tags
   }
   ```

3. **For resources requiring a `Name` tag or other resource-specific tags**, merge with base tags:
   ```hcl
   resource "aws_vpc" "main" {
     # ... resource configuration ...
     tags = merge(
       local.base_tags,
       {
         Name = "${var.StackIdentifier}-vpc"
       }
     )
   }
   ```

4. **Tag application rules:**
   - Apply tags to all resources that support the `tags` attribute (VPC, subnets, security groups, RDS instances, S3 buckets, ECS clusters, etc.)
   - The `Name` tag value must include the `StackIdentifier` prefix for resource identification
   - Do **not** add custom tags beyond `Name` unless explicitly required by the task
   - Existing templates with inline tag blocks must be refactored to use this pattern
   - Before adding a `tags` block to any resource, the agent must explicitly confirm that the resource type supports tagging. If the resource does not support tags, do not add the `tags` attribute and do not attempt to merge base tags.

This standard ensures consistent resource tagging across all stacks, enables cost tracking and resource filtering, and eliminates tag-related drift.

### Terraform Header Requirement
- Every `stack.tf` file must begin with the following header block:
  ```hcl
  terraform {
    required_version = ">= 1.6.0"
    
    backend "s3" {
      bucket         = "<account_specific_bucket_name>"
      dynamodb_table = "opentofu-locks"
      encrypt        = true
    }
    
    required_providers {
      aws = {
        source  = "hashicorp/aws"
        version = "~> 5.0"
      }
    }
  }
  ```
- This block standardizes backend configuration, provider versioning, and ensures consistent setup across all Erie Iron OpenTofu stacks.
- Do **not** modify the bucket, region, or provider source/version unless explicitly authorized by the infrastructure maintainer.
- <account_specific_bucket_name> is dynamic based on the account
- **Never** modify this header block once it is written

### S3 Versioning Rule
- OpenTofu provider v5.x and above no longer supports defining S3 bucket versioning directly within the `aws_s3_bucket` resource.
- Instead, versioning must be defined using a separate `aws_s3_bucket_versioning` resource.
- When adding or modifying S3 bucket resources, always ensure there is a corresponding `aws_s3_bucket_versioning` resource with a matching bucket reference and explicit versioning configuration block:
  ```hcl
  resource "aws_s3_bucket_versioning" "example" {
    bucket = aws_s3_bucket.example.id
    versioning_configuration {
      enabled = false
    }
    lifecycle {
      prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
    }
  }
  ```
- Do not use the deprecated `versioning {}` block inside `aws_s3_bucket`.
- The `aws_s3_bucket_versioning` resource must always include a `lifecycle` block consistent with Erie Iron conventions (`prevent_destroy = ERIE_IRON_RETAIN_RESOURCES`).

- Default the AWS region to us-west-2 unless specifically instructed otherwise
- Provisioning plans must prioritize cost-efficiency and security:
  - When choosing AWS services (e.g., App Runner vs ECS vs Lambda), select the **least expensive** option that satisfies load and runtime needs.
  - When provisioning instance-based services (e.g., RDS, EC2), use the **smallest available instance type** that can fulfill the requirements.
  - For test environments, prefer options like `db.t4g.micro`, `t4g.nano`, or similarly low-cost configurations.
  - Avoid overprovisioning or selecting higher tiers by default.
  - IAM roles must follow the **principle of least privilege**—grant only the permissions required to perform the specific task.
- Erie Iron maintains **one** OpenTofu template, stored at `opentofu/application/stack.tf`:
  - `opentofu/application/stack.tf` contains all infrastructure resources including persistent components (RDS, SES identities, Route53 verification records, SSM parameters) and application delivery components (ALBs, listeners, target groups, ECS services, task roles, Lambdas, log groups, DNS aliases, etc.). This unified stack is namespaced to the active task in DEV and to the business in PROD.
- All infrastructure resources should be placed in the single APPLICATION stack template.
- Keep stack names stable; only trigger stack rotation as a recovery fallback when deletes or updates wedge in a terminal rollback state.
- ECS/Fargate task definitions must always:
    - Configure `DeploymentConfiguration` with:
        - `MaximumPercent: 100`
        - `MinimumHealthyPercent: 100`
        - `DeploymentCircuitBreaker` set to `Enable: true` and `Rollback: true`
      This combination ensures ECS fails fast and rolls back immediately if any task fails to start or becomes unhealthy.
    - Add a `CreationPolicy` with a `ResourceSignal` timeout (e.g., `PT10M`) on ECS services so that OpenTofu stack updates fail quickly if the service cannot stabilize.
    - (Optional but recommended) Include a `WaitCondition` that requires the container to signal success upon startup using a OpenTofu WaitConditionHandle URL; omit it only for non-long-running worker tasks.
- If deployment or infrastructure provisioning fails, it must be fixed before proposing any other code changes.
- If a parameter becomes required, but its OpenTofu description still includes '(optional)', remove the '(optional)' label to reflect its new required status.
- All resources must specify deletion policies that ensure clean, autonomous stack lifecycle management. Do not hardcode `Retain` or `Delete`; always wire the stack's `DeletePolicy` parameter so orchestration can control retention per environment.
    - **All resources must set both `DeletionPolicy` and `UpdateReplacePolicy` to `!Ref DeletePolicy`.**
    - **Every resource with an explicit `prevent_destroy` in a `lifecycle` block must set `prevent_destroy = ERIE_IRON_RETAIN_RESOURCES`.** When you add or edit a block, format it like this:
      ```hcl
      lifecycle {
        prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
      }
      ```
      The agent rewrites `ERIE_IRON_RETAIN_RESOURCES` during preprocessing, so do not substitute other values or reference template parameters here.
    - **For any SSM parameter (`AWS::SSM::Parameter`) that references other resources (such as referencing Lambda ARNs, Role ARNs, Bucket names, etc.), add a `DependsOn` relationship to those referenced resources so SSM parameters are deleted after the resources they reference.**
    - **For Lambda functions (`AWS::Lambda::Function`), add a `DependsOn` to ensure the function is deleted before any IAM roles it uses. This ensures IAM roles are not deleted while the Lambda function still exists.**
    - **S3 buckets must always set `DeletionPolicy: !Ref DeletePolicy`. The self-driving deployment agent empties stack buckets before deletion, so do not introduce additional cleanup Lambdas or lifecycle rules unless specifically required by a task.**
    - **For IAM roles, ensure that no inline policies or external policy attachments prevent deletion. All policies must be removed (or detached) before the role can be deleted.**
    - **OpenTofu stacks must be able to delete cleanly in all environments, with no manual resource cleanup or intervention ever required.**
- Erie Iron deploys every stack inside the shared VPC `erie-iron-shared-vpc`. Plans and templates must rely on `VpcId`, `PublicSubnet{1,2}Id`, `PrivateSubnet{1,2}Id`, and `VpcCidr` parameters and must not create or modify VPCs, subnets, route tables, internet gateways, NAT gateways, or VPC endpoints.

-### S3 Event Notification Standard
-### Duplicate Security Group Rule Guidance
- When defining `aws_security_group_rule` resources, ensure each rule is unique per `(protocol, from_port, to_port, source)` tuple.
- If a shared Security Group already includes the rule, **do not re-create it** in downstream stacks.
- To prevent duplicate rule errors, always:
  - Prefer **conditional creation** using a `count` guard variable (e.g., `count = var.CreateIngressRule ? 1 : 0`) when multiple stacks might target the same Security Group.
  - Avoid defining identical ingress or egress rules in multiple modules pointing to the same Security Group ID.
  - For shared security groups, manage rules in the **APPLICATION stack** as all security group resources are now co-located.
- When a duplicate rule error occurs (e.g., `InvalidPermission.Duplicate`), do **not** modify the Security Group manually. Instead:
  - Identify the conflicting rule via `aws ec2 describe-security-group-rules --filters Name=group-id,Values=<sg-id>`.
  - Remove or conditionally disable the redundant rule in the relevant stack template.
- Example conditional block:
  ```hcl
  variable "CreateIngressRule" {
    description = "Whether to create this ingress rule (useful when SG is shared)."
    type        = bool
    default     = true
  }

  resource "aws_security_group_rule" "example_ingress" {
    count             = var.CreateIngressRule ? 1 : 0
    security_group_id = var.SecurityGroupId
    type              = "ingress"
    from_port         = 8006
    to_port           = 8006
    protocol          = "tcp"
    cidr_blocks       = [var.VpcCidr]
  }
  ```
- Always prefer **shared guardrails** and **consistent naming conventions** to avoid state drift or overlapping rules.
- Always configure S3 -> Lambda/SNS/SQS notifications using the inline `NotificationConfiguration` property on the `AWS::S3::Bucket` resource.
  - There is no `AWS::S3::BucketNotification` resource type in OpenTofu.
  - Configure notifications directly within the bucket definition using the `NotificationConfiguration` property.
  - Example:
    ```yaml
    EmailIngestBucket:
      Type: AWS::S3::Bucket
      Properties:
        BucketName: !Sub "${StackIdentifier}-email-ingest"
        NotificationConfiguration:
          LambdaConfigurations:
            - Event: "s3:ObjectCreated:*"
              Filter:
                S3Key:
                  Rules:
                    - Name: prefix
                      Value: inbound/
                    - Name: suffix
                      Value: .eml
              Function: !GetAtt EmailIngestionLambda.Arn
    ```
- Ensure proper Lambda invoke permissions are defined separately using `AWS::Lambda::Permission` resources that grant `s3.amazonaws.com` permission to invoke the target Lambda.

- When writing or updating `opentofu/application/stack.tf`, **always** include an ingress rule that allows the ALB to reach ECS tasks from within the shared VPC. This rule must look exactly like this:

  ```yaml
  EcsIngressFromVpc:
    Type: AWS::EC2::SecurityGroupIngress
    Properties:
      GroupId: !Ref SecurityGroupId
      IpProtocol: tcp
      FromPort: 8006
      ToPort: 8006
      CidrIp: !Ref VpcCidr
      Description: Allow ALB to reach ECS tasks on port 8006 from within the VPC
  ```
  
  This ensures every web service can respond to ALB health checks and traffic within the shared network. Do **not** hardcode any source CIDRs or security group IDs for this rule—always use `!Ref VpcCidr`.
- ECS/Fargate web services must run inside this shared VPC using the provided private subnets. Configure `AwsvpcConfiguration.Subnets` with `!Ref PrivateSubnet1Id` and `!Ref PrivateSubnet2Id` (keeping `AssignPublicIp: DISABLED`) so tasks stay on the internal network.
- Proposals should scope networking changes to stack-owned resources such as security groups, ECS services, and ALB listeners. Route53 subdomain routing supplies tenant isolation for tenants sharing the same VPC.
    - `opentofu/application/stack.tf` owns both the initiative-level root domain used for SES verification and publishes ALB aliases on task-scoped subdomains. Preserve the `DomainName` parameter as the bare domain (e.g., `initiative.example.com`) and derive task subdomains as needed (e.g., `${StackIdentifier}.${DomainName}`). Do **not** register unrelated subdomains or hardcode alternate roots.
- The Dockerfile **must always** extend this base image: "782005355493.dkr.ecr.us-west-2.amazonaws.com/base-images:python-3.11-slim"
- You can safely ignore this warning:  "WARNING: The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8)"
- If Lambda code requires `AWS_DEFAULT_REGION` or `AWS_REGION`, the OpenTofu configuration must pass these in from the `${AWS::Region}` variable.

- If `DomainName` is managed in Route53 in the same AWS account, publish SES verification TXT/DKIM/MX records and ALB-facing alias records in `opentofu/application/stack.tf` using the task subdomain described above. All DNS automation for the provided `DomainName` must live in the stack—no manual zone edits.


- **Route53 TXT Record Quoting Rule**  
  - When defining TXT records in OpenTofu (for DMARC, SPF, SES verification, etc.), do **not** include extra inner quotes within the `records` value.  
  - Correct form: `records = ["v=DMARC1; p=quarantine; rua=mailto:postmaster@${var.DomainName}"]`  
  - Incorrect form (causes `InvalidCharacterString`): `records = ["\"v=DMARC1; p=quarantine; rua=mailto:postmaster@${var.DomainName}\""]`  
  - Terraform automatically wraps TXT record strings in quotes when applying, so inner quoting leads to double-quoted invalid values.

### Route53 Allow Overwrite Rule
- All `aws_route53_record` resources must include `allow_overwrite = true`.
- This ensures stacks can safely adopt pre-existing SES, DKIM, SPF, DMARC, and verification records without manual cleanup.
- Required for all TXT and CNAME records created for SES validation, DKIM tokens, SPF, DMARC, and any other stack-managed DNS records.
- Omission of this property will cause `InvalidChangeBatch` errors when records already exist in the hosted zone.

- Email ingestion requirements mean the stacks must provision an `AWS::SES::ReceiptRuleSet` (namespaced with `!Ref StackIdentifier`) and one or more `AWS::SES::ReceiptRule` resources that deliver to the task-specific targets (S3 buckets, Lambdas, SNS). Do not leave the rule set empty.
- Include a `Custom::ActivateSesRuleSet` (or equivalent Lambda-backed custom resource) that calls `ses:SetActiveReceiptRuleSet` with the stack-owned rule set on create/update so it becomes the account's active set. On delete, the same custom resource must clear the active rule set back to `""` before OpenTofu deletes the receipt rule set. Wire explicit `DependsOn` relationships so activation waits for the rule set and rules to exist and deactivation happens before the rule set is removed.
- If `DomainName` is not in Route53, return `blocked` with `category: "infra_boundary"` and instructions to onboard the domain to Route53 instead of scheduling HUMAN_WORK.
- When deleting SES ReceiptRuleSets, you must call `ses:SetActiveReceiptRuleSet` with `"RuleSetName": ""` via a custom resource (e.g., `Custom::ActivateSesRuleSet`) before attempting deletion so OpenTofu can cleanly remove the rule set. Always add a `DependsOn` from the ReceiptRuleSet to the deactivation resource.
- If a ReceiptRuleSet resource is observed in DELETE_FAILED with "Cannot delete active rule set", ensure the stack configuration includes a `Custom::ActivateSesRuleSet` resource that calls `ses:SetActiveReceiptRuleSet` with `"RuleSetName": ""` before deletion, and add `DependsOn` from the ReceiptRuleSet to this deactivation resource.
- Must: Clear SES active rule set before delete.
- Forbidden: Deleting an SES rule set while it is still active.


### Dynamic Resource Key Guardrail
- Never use a `for_each` or `count` that depends on values known only after apply (for example, `.id`, `.arn`, `.dns_name`, `.domain_validation_options`, `.dkim_tokens`, etc.). These cause "Invalid for_each argument" errors because OpenTofu cannot determine the resource keys during plan time.
- The `for_each` keys must always be deterministic at plan time.
- When values are not known until apply:
  - Use a static key list (e.g. `["0", "1", "2"]`) or a map of known keys to placeholder values.
  - Or split the deployment into two stages: first create the producing resource, then create the dependent ones.

  - **General Rule for Dependent Resource Creation**
    - Never derive `for_each` or `count` values from attributes that are computed only after apply, such as `.id`, `.arn`, `.dns_name`, `.domain_validation_options`, `.dkim_tokens`, or any resource-specific token list.
    - This includes all SES, ACM, ALB, Lambda, IAM, and ECS resource types that generate tokens or IDs asynchronously.
    - When such attributes are required (for example, DKIM tokens or ACM validation records), use a static placeholder map or deterministic index keys, as shown in the DKIM example below.
    - If unsure whether a value is known at plan time, treat it as unknown and plan a two-phase apply instead (produce → consume).

- Common offenders include: `aws_ses_domain_dkim`, `aws_acm_certificate`, `aws_lb`, `aws_lambda_function`, `aws_iam_role`, and `aws_ecs_service`.
- Example (DKIM-specific pattern):

    ```hcl
    locals {
      dkim_record_keys = ["0", "1", "2"]
    }

    resource "aws_route53_record" "dkim" {
      for_each = local.hosted_zone_provided ? {
        for k in local.dkim_record_keys :
        k => aws_ses_domain_dkim.this.dkim_tokens[tonumber(k)]
      } : {}

      zone_id = var.DomainHostedZoneId
      name    = "${each.value}._domainkey.${var.DomainName}"
      type    = "CNAME"
      ttl     = 300
      records = ["${each.value}.dkim.amazonses.com"]
    }
    ```
- This ensures deterministic planning and prevents "Invalid for_each argument" errors in any resource where output-based iteration would otherwise occur.



### Sensitive Output Rule
- Outputs that include secrets or private data must be marked with `sensitive = true`.
- This flag hides secret values from CLI output, state files, and dependent stacks.
- Mark an output as sensitive if it includes:
  - Secret ARNs, secret names, passwords, access tokens, or keys.
  - Any value retrieved from Secrets Manager or SSM Parameter Store that contains credentials.
- Examples that **must** include `sensitive = true`:
  ```hcl
  output "RdsMasterSecretArn" {
    value       = try(aws_db_instance.primary.master_user_secret[0].secret_arn, null)
    sensitive   = true
  }
  ```
- Examples that **should not** include `sensitive = true`:
  - Identifiers (e.g., `RdsInstanceIdentifier`)
  - Hostnames or endpoints (e.g., `RdsInstanceEndpoint`)
  - Ports, DB names, or zone IDs
- Overuse of `sensitive` complicates debugging by hiding harmless values.
- **Rule:** mark only genuinely secret data as sensitive, not general identifiers or connection metadata.

### Region Output Rule
- Every `stack.tf` file must output the AWS region being used by the provider.
- This output must be named **AwsRegion** exactly.
- The output value must be wired to the standard region data source:
  ```hcl
  output "AwsRegion" {
    description = "AWS region being used by the provider."
    value       = data.aws_region.current.name
  }
  ```
- This output is required for all stacks with no exceptions.

### SecretsManager Recovery Window Rule
- When creating `aws_secretsmanager_secret` resources, you **must** express the recovery window using a single HCL conditional expression:
  ```hcl
  recovery_window_in_days = var.DeletePolicy == "Delete" ? 0 : 7
  ```
- Do **not** duplicate the resource or create conditional counts. Always use the ternary expression shown above.
- This rule guarantees that secret retention behavior aligns with the stack's deletion policy without branching logic.
- If you find an `aws_secretsmanager_secret` definition that is missing this expression or using a hardcoded value, **YOU MUST** update it to match the exact syntax above.

### OpenTofu File Enforcement
- Keep OpenTofu definitions inside the single stack template at `opentofu/application/stack.tf`—all infrastructure resources including both persistent and application delivery components belong here.
- Inline IAM policy attachments (e.g., `AWS::IAM::Policy` targeting stack-defined roles) belong next to the role in whichever template owns it; runtime code must not create or modify IAM.
- **When attaching IAM policies:**  
    - Prefer `Roles: [!Ref <RoleLogicalId>]` when the role is defined in this template and assigns a concrete `RoleName`.  
    - Use `RoleArns` only when you must reference an external ARN (avoid this path unless explicitly required).  
    - **Mixing `Roles` and `RoleArns` is strictly forbidden** and will cause deployment failures. Always choose the property that matches the value you provide.
- Creating or modifying any other OpenTofu YAML file is a violation.
- Creating or modifying any other OpenTofu YAML file is a violation.
- If a plan attempts to edit a different file, redirect it to the correct stack template—do **not** return `blocked`.

- **IAM roles or permissions related Tasks**
    - Follow the principle of least privilege: include only permissions essential to accomplish the task.
    - Identify all required permissions up front to avoid iteration churn due to missing access
- **Database-Related Tasks**
    - Use AWS RDS for PostgreSQL as the database backend in **all environments**, including development and test.
    - use PostgreSQL engine version >= 16.8
    - Do not assume or configure any locally running PostgreSQL service.
    - Source all connection details from environment variables or AWS Secrets Manager.
- RDS security groups must explicitly allow Postgres (tcp/5432) from the stack's web service security group while keeping CIDR rules tightly scoped:
    - Add an ingress rule referencing the web ECS security group (e.g., `SourceSecurityGroupId: !Ref WebServiceSecurityGroup`) so application tasks can reach the database.
    - Limit any CIDR-based ingress to the shared network (`!Ref VpcCidr`) and the `ClientIpForRemoteAccess` parameter; never broaden beyond these ranges.

### OpenTofu Update Efficiency Guidelines
Plans must avoid replacement of stateful resources. If replacement is unavoidable, the plan must:
1) mark will_replace: true for each affected resource,
2) explain the justification in plan.rationale.updates,
3) provide a rollback path.
Inline `AWS::IAM::Policy` attachments should be isolated so they do not introduce replacements or `DependsOn` chains to slow resources like RDS.

### RDS Configuration
The RDS opentofu configuration should always look like this:
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
      VPCSecurityGroups: [!Ref SecurityGroupId ]
      DBSubnetGroupName: !Ref DBSubnetGroup
      BackupRetentionPeriod: 7
      MultiAZ: false
      PubliclyAccessible: true
      StorageEncrypted: true
      Tags:
        - Key: Name
          Value: !Sub ${StackIdentifier}-db-instance
```

- The `DBSubnetGroup` defined in `opentofu/application/stack.tf` must list `!Ref PublicSubnet1Id` and `!Ref PublicSubnet2Id` so the database resides in the public subnets and remains reachable from JJ's laptop. Any historical guidance that mentioned private subnets is **deprecated**.
  - Migration steps when you encounter a private-subnet DB subnet group:
    1. Update the `DBSubnetGroup` resource to reference `PublicSubnet1Id` / `PublicSubnet2Id` exactly as shown below.
    2. Confirm the associated security group still allows developer CIDR + application ingress on tcp/5432.
    3. Run the stack update and monitor the change set to ensure OpenTofu performs an in-place subnet swap (no replacement) before continuing with application changes.

  DBSubnetGroup should look like this (note the public SubnetIds):
```yaml
  DBSubnetGroup:
    Type: AWS::RDS::DBSubnetGroup
    Properties:
      DBSubnetGroupDescription: Subnet group for RDS database within the shared Erie Iron VPC
      SubnetIds:
        - !Ref PublicSubnet1Id
        - !Ref PublicSubnet2Id
      Tags:
        - Key: Name
          Value: !Sub ${StackIdentifier}-db-subnet-group
```
- Always keep `PubliclyAccessible: true` on the RDS instance so the shared security group and developer IP CIDR rules continue to allow direct access during development.

### RDS Secret Management Contract
- Use `ManageMasterUserPassword: true` in `AWS::RDS::DBInstance`.
- The DBInstanceIdentifier must be parameterized (e.g. `!Sub "${StackIdentifier}-db"`) to namespace the auto-generated secret.
- OpenTofu must output `!GetAtt RDSInstance.MasterUserSecret.SecretArn` **only**; do not create a `RdsMasterSecretName` (or any secret name) output parameter.
- No `Parameters.RdsSecretArn`, no `SecretTargetAttachment`, and no Lambda custom resource are required for this pattern.
- The shared security group referenced by `SecurityGroupId` already permits safe access; do not generate stack-specific security groups unless explicitly required.

### RDS Application Environment Wiring
- Every ECS/Fargate task definition or container that connects to this database must define the following environment
  variables alongside `RDS_SECRET_ARN` in `ContainerDefinitions[].Environment`:
    - `ERIEIRON_DB_NAME`: usually wired to the literal `appdb` value (or the stack’s DBName parameter).
    - `ERIEIRON_DB_HOST`: `!GetAtt RDSInstance.Endpoint.Address`.
    - `ERIEIRON_DB_PORT`: `!GetAtt RDSInstance.Endpoint.Port` (do not hardcode 5432).
- These variables ensure Django’s settings can construct the connection parameters via `os.getenv`. Plans and templates
  must fail review if any database-connected service omits them.

### RDS Secret Attachment Requirements
- Separate DB instance creation from AWS::SecretsManager::SecretTargetAttachment where possible.
- Detect existing attachment and no-op rather than re-attach.
- Avoid DependsOn unless required by a specific readiness condition. Include a readiness comment when DependsOn is used.

When planning or modifying AWS OpenTofu templates:

1. **Single-stack integrity**  
   All resources for a given business/environment must remain in a single stack unless explicitly instructed otherwise.

2. **Stack-managed roles**  
   OpenTofu must create and manage the IAM roles it needs inside this template.  
   - Each role sets `RoleName: !Sub "${StackIdentifier}-..."` (or equivalent) and respects the 64-character limit.  
   - resources should reference roles with `!GetAtt <Role>.Arn`.  
   - Inline policies must remain least privilege with justification comments.

3. **No-replacement preference**  
   For slow-to-create resources (e.g., RDS databases, ALBs, large ECS services), prefer property changes that avoid `Replacement`.  
   Consult AWS’s update behavior for each resource type and select properties that can update `Without interruption` or `Some interruption` instead of replacement.

4. **Parallelism maximization**  
   Remove unnecessary `DependsOn` and avoid creating implicit dependencies between unrelated resources.  
   Structure the template so OpenTofu can update independent resources concurrently.

5. **Change scope minimization**  
   Avoid changes that trigger updates to unchanged resources (including tags or metadata) unless functionally required.  
   This helps keep updates scoped and faster.

6. **Change set review**  
   Plan for the deployment process to use Change Sets to preview the blast radius.  
   Reject or re-plan if a change set shows unexpected replacements or broad tag churn.

7. **Drift-free baseline**  
   Assume the stack is kept drift-free; do not rely on out-of-band modifications.

---

## OpenTofu Resource Durations Input

You may be given a `opentofu_durations` list: a JSON array of objects, each with:

```json
[
  {
    "logical_id": "string",         // OpenTofu LogicalResourceId
    "resource_type": "string",      // OpenTofu ResourceType
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

Your goal is to plan precise and deterministic infrastructure provisioning changes to resolve the diagnosed AWS error. This may include creating or updating IAM roles, S3 buckets, Lambda configuration, OpenTofu resources, or Dockerfile environment wiring. You are not planning general application fixes—your scope is limited to infrastructure and provisioning issues only.

---

## OpenTofu yaml Parsing

When parsing any yaml, you **must** use:

```python
from erieiron_public import agent_tools
agent_tools.parse_opentofu_yaml(Path(<path to yaml>))  # ✅ Correct
```

### Prohibited Alternatives
- Do **not** use `yaml.safe_load`, `yaml.load`, or any PyYAML loader.
- Do **not** attempt to implement a custom parser for OpenTofu YAML.
- The only valid parser is `agent_tools.parse_opentofu_yaml`.

### Incorrect Example
```python
yaml.safe_load(Path(<path to yaml>).read_text())  # ❌ Forbidden
```

---

## Resource Name Namespacing

- All AWS resource names (S3, SQS, etc.) must be namespaced with the StackIdentifier, for example: `!Sub "${StackIdentifier}-<resource_name>"`.
    - If you discover resource names in either stack template that are not namespaced, you **must** fix them by namespacing them.
    - The only exception is `RDS DBName`, which must always be hardcoded to `appdb`.

---

## Required Parameters
- The following parameters are required in both stack templates. 
- This section **must** be written **exactly** as follows with **no modifications**
- IAM roles are created within the stack
```yaml
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
  DomainName:
    Type: String
    Description: "Required: The task-specific subdomain to use for DNS and SES configuration."
  DomainHostedZoneId:
    Type: String
    Description: "Required: The Route53 hosted zone ID in which the DomainName record should be created."
  AlbCertificateArn:
    Type: String
    Description: "Required: ACM certificate ARN that covers the specified DomainName."
  WebContainerImage:
    Type: String
    Description: "Required: Full container image URI (for example, an ECR image) that serves the Django application."
  WebContainerCpu:
    Type: Number
    Default: 512
    Description: "Fargate task CPU units for the web service."
  WebContainerMemory:
    Type: Number
    Default: 2048
    Description: "task memory (MiB) for the web service."
  WebDesiredCount:
    Type: Number
    Default: 1
    MinValue: 1
    Description: "task count for the service."
  VpcId:
    Type: String
    Description: 'ID of the shared Erie Iron VPC (ex: erie-iron-shared-vpc).'
  PublicSubnet1Id:
    Type: String
    Description: 'Public subnet 1 inside the shared VPC.'
  PublicSubnet2Id:
    Type: String
    Description: 'Public subnet 2 inside the shared VPC.'
  PrivateSubnet1Id:
    Type: String
    Description: 'Private subnet 1 inside the shared VPC.'
  PrivateSubnet2Id:
    Type: String
    Description: 'Private subnet 2 inside the shared VPC.'
  VpcCidr:
    Type: String
    Default: '10.90.0.0/16'
    Description: 'CIDR block of the shared VPC for security group rules.'
  SecurityGroupId:
    Type: String
    Description: 'Shared security group that allows database connectivity from Erie Iron infrastructure.'
```

- `DomainName` and `DomainHostedZoneId` drive Route53 records (root alias, SES verification, MX, DKIM) and must be referenced by any DNS resources you add to the template.
- `AlbCertificateArn` attaches the validated ACM certificate to the ALB listener so HTTPS works for the chosen domain.
- `WebContainerImage` is the fully qualified image URI the ECS service should deploy.
- `WebContainerCpu`, `WebContainerMemory`, and `WebDesiredCount` configure the ECS Fargate task definition and service capacity; wire them directly into the TaskDefinition and Service resources instead of hardcoding values.

### Route53 Root Alias Guardrail
- Treat `DomainName` as the publicly routed hostname (often a subdomain). When directing traffic to an Application Load Balancer, create `AWS::Route53::RecordSet` resources with `Type: A` **and** `Type: AAAA` that use `AliasTarget` pointing to the ALB’s `DNSName` and `CanonicalHostedZoneID` attributes.
- Reuse the existing hosted zone associated with the business domain (or the Erie Iron fallback zone) for these records; do **not** create new hosted zones for task-specific subdomains.
- **Never** emit a `Type: CNAME` record whose `Name` resolves to `!Ref DomainName`, even if the value contains dots. Apex-style aliases keep TLS and health checks stable and avoid resolver rejection of CNAME-at-apex records.
- Do not populate `ResourceRecords` for these Route53 alias records—the alias target must supply the ALB hostname. Continue using CNAMEs only for tokenized sub-records such as SES DKIM entries (`${Token}._domainkey.${DomainName}`).
- Ensure the alias Route53 resources are conditionally created when a hosted zone ID is supplied (for example, gated by a `HasHostedZone` condition) so stacks behave correctly when DNS automation is disabled.

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
- **Never** create DomainAliasRecord or related.  Domain management is handled by the orchestration layer
- **Never** create IAM roles whose `RoleName` omits the `!Ref StackIdentifier` prefix or exceeds 64 characters. Inline `AWS::IAM::Policy` resources are allowed only when they target stack-defined roles, use least-privilege statements, and include justification comments.
- **Never** generate or plan direct interactions with AWS services via the `boto3` client for infrastructure management.
- **Never** define a Lambda function with an environment variable name beginning with `AWS_`.  
    - These prefixes are reserved by AWS and will cause the OpenTofu deployment to fail.
- **Never** introduce a new OpenTofu parameter without a default value.  
    - If a new parameter is needed, you **must** supply a default.  
    - If no suitable default can be provided, you must raise `agent blocked` instead of generating the parameter.  
- **Never** hardcode resource names (like S3 bucket names, SQS queue names, etc. - this applies to **any and all** named aws service or resources - the only exception is RDS DBName, which is always `appdb`)  
    - all resource names **must** be namespaced with the StackIdentifier - eg `!Sub "${StackIdentifier}-<resource_name>"`
    - if you discover hardcoded resource names in either stack template, you **must** fix them by namespacing them with `!Sub "${StackIdentifier}-<resource_name>"`
- **Never create Route53 subdomain or alias records (e.g., `${StackIdentifier}.${DomainName}`) in infrastructure templates. Subdomain management occurs outside the stack.**
