## IAM Role Usage Contract 

The system enforces a single-role model per {business, env}. The role is **constructed outside** CloudFormation and is **always** provided to the template as the required parameter `TaskRoleArn`.

**Required wiring**
- ECS task definitions:
  - `TaskRoleArn: !Ref TaskRoleArn`
  - `ExecutionRoleArn: !Ref TaskRoleArn`
- Lambda functions:
  - `Role: !Ref TaskRoleArn`
- Any other service fields that accept a role ARN must also reference `!Ref TaskRoleArn`.

**Permitted inline policy attachments**
- You may add `AWS::IAM::Policy` resources only when `Properties.Roles` is exactly `[!Ref TaskRoleArn]`.
- For Lambda permissions:
  - When using `AWS::Serverless::Function`, you may use the resource’s `Policies` property with statements scoped to the function.
  - For `AWS::Lambda::Function`, attach permissions by creating an inline `AWS::IAM::Policy` resource that targets `TaskRoleArn`.
- All policies must follow least privilege: enumerate the minimal `Action` list and scope `Resource` ARNs to the specific function, queue, topic, secret, or log group.
- Every `Action`/`Resource` pair requires a justifying YAML comment that names the target function/service and why the access is needed.
- You may not create any other roles or attach policies to ARNs that are not `!Ref TaskRoleArn`.

**Prohibited**
- Creating new roles or instance profiles (`AWS::IAM::Role`, `AWS::IAM::InstanceProfile`).
- Attaching IAM policies (including `AWS::IAM::Policy` or resource `Policies` blocks) to any role other than `!Ref TaskRoleArn`.
- Introducing parameters that select or generate alternate roles, or adding `AllowedPattern` to `TaskRoleArn`.
  - If the existing `TaskRoleArn` parameter definition has an `AllowedPattern` value, **you must** remove it.

**Explicitly allowed**
- Inline `AWS::IAM::Policy` resources that attach to `Roles: [!Ref TaskRoleArn]` and comply with the least-privilege and justification rules above.

**Assumptions**
- Trust and permissions for the provided role are managed outside this template unless you explicitly attach least-privilege inline policies as described above.

## SES Automation Contract
- SES domain identity and DKIM tokens must be surfaced as CloudFormation outputs, and the same template must create the corresponding Route53 `AWS::Route53::RecordSet` resources. HUMAN_WORK for SES activation is disallowed; automate or emit `blocked` with a clear infra boundary reason.

---

## Permissions Attachment Rules
- VPC-configured Lambdas **must** include ENI permissions on `TaskRoleArn`: `ec2:CreateNetworkInterface`, `ec2:DescribeNetworkInterfaces`, `ec2:DeleteNetworkInterface`, `ec2:AssignPrivateIpAddresses`, `ec2:UnassignPrivateIpAddresses`.
  - Where an action requires `Resource: "*"`, include a comment noting the AWS scoping limitation and add a `Condition` (e.g., `aws:Vpc`, `aws:ResourceTag`) when possible.
- CloudWatch Logs access must include only `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents`, scoped to the Lambda log group ARN(s).
- RDS secrets access must use `secretsmanager:GetSecretValue` scoped to `!GetAtt RDSInstance.MasterUserSecret.SecretArn` (or an equivalent specific ARN).
- Each policy statement requires a comment explaining which function or resource needs the permission and why.
- Scope `Resource` values to concrete ARNs whenever the API allows it; default to comments plus conditions when AWS demands `*`.

## Role Permissions WARNINGS and ERRORS
Consider any AWS role or permissions WARNING or ERROR messages as likely root cause errors. (Even if it's reported as a warning.)
- If a Lambda resource gains or modifies `VpcConfig`, verify an inline `AWS::IAM::Policy` in this stack grants the ENI permissions above to `TaskRoleArn` unless documented that the external role already includes `AWSLambdaVPCAccessExecutionRole`. Default to adding the inline policy for repeatability.
- **You must** prioritize fixing these role permissions before attempting other changes.

## Validation Checklist
- Confirm every `AWS::IAM::Policy` targets `Roles: [!Ref TaskRoleArn]` exclusively.
- Verify each policy uses least-privilege `Action` values and the narrowest possible `Resource` ARNs.
- Check that each statement has a justification comment for both `Action` and `Resource` scope.
- Ensure no policies introduce unrelated service permissions or unbounded wildcards without conditions and comments.
- Fail the plan if any IAM policy targets a different role or adds unmanaged wildcards.

## Reviewer Checklist
- Change sets should show only the expected inline `AWS::IAM::Policy` resource additions or updates—never new role creations or replacements.
- Confirm no slow resources (e.g., RDS) appear in the replacement list as a side effect of policy attachment changes.

### Governance Decision Gate (optional)
If organizational policy forbids in-stack IAM policy attachments, emit `blocked` with `category: "permissions"` and include the exact policy JSON so it can be applied out of band. Otherwise, follow the contract above to attach policies in `infrastructure.yaml`.
