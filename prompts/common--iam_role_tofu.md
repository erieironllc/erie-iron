## IAM Role Usage Contract 

OpenTofu stacks must create and manage every IAM role they need. The legacy `TaskRoleArn` parameter is forbidden—remove it when present and rely on in-stack role resources instead.

**Role creation rules**
- Define roles with `AWS::IAM::Role`; do not assume an external role exists.
- Set `RoleName: !Sub "${StackIdentifier}-${Suffix}"` (or an equivalent expression) so every role name:
  - begins with the current stack identifier;
  - stays within AWS's 64-character limit (shorten the suffix as needed);
  - uses only characters allowed by IAM. Sanitize spaces/underscores to hyphens and keep names lowercase when practical.
- Reuse the same role when multiple resources share identical permissions; otherwise create purpose-specific roles following the same prefix rule.

**Required wiring**
- ECS task definitions must point both `TaskRoleArn` and `ExecutionRoleArn` at the ARN of a role defined in this template (e.g., `!GetAtt ApiTaskRole.Arn`).
- Lambda functions must reference the stack-defined role via `Role: !GetAtt <RoleLogicalId>.Arn`.
- Any property that expects a role ARN must resolve to one of the template-defined roles.

**Permitted inline policy attachments**
- Attach managed permissions with `AWS::IAM::Policy` resources targeting the logical IDs of roles created in this stack (`Roles: [!Ref <RoleLogicalId>]`).
- For `AWS::Serverless::Function`, the `Policies` block may be used when statements map to the Lambda's role and obey least-privilege rules.
- Each statement must use the minimal `Action` set, scope `Resource` values as tightly as AWS allows, and include a justification YAML comment naming the principal and operation.

**Prohibited**
- Using any out-of-stack role.
- Creating roles whose names do not start with the stack identifier or that exceed 64 characters.
- Leaving permissions overly broad (e.g., `Action: '*'` or `Resource: '*'`) without unavoidable justification and conditions.

**Explicitly allowed**
- Defining multiple IAM roles as long as they follow the prefix and length rules.
- Using inline or managed policies that attach to the stack-defined roles and comply with least-privilege guidance.

**Assumptions**
- Trust policies typically need `lambda.amazonaws.com` and/or `ecs-tasks.amazonaws.com` principals. Add others only when the service truly requires them.

## SES Automation Contract
- SES domain identity and DKIM tokens must be surfaced as OpenTofu outputs, and the same template must create the corresponding Route53 `AWS::Route53::RecordSet` resources. HUMAN_WORK for SES activation is disallowed; automate or emit `blocked` with a clear infra boundary reason.

---

## Permissions Attachment Rules
- Lambdas configured with `VpcConfig` must receive ENI permissions on their associated role: `ec2:CreateNetworkInterface`, `ec2:DescribeNetworkInterfaces`, `ec2:DeleteNetworkInterface`, `ec2:AssignPrivateIpAddresses`, `ec2:UnassignPrivateIpAddresses`.
  - When AWS requires `Resource: "*"`, add a comment explaining the scoping limitation and prefer adding a `Condition` (e.g., `aws:Vpc`, resource tags) when possible.
- CloudWatch Logs access should be limited to `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents`, scoped to the Lambda log-group ARN(s).
- Secrets Manager access must scope `secretsmanager:GetSecretValue` to the specific secret ARN(s) used by the workload.
- Each policy statement needs a comment describing which resource consumes the permission and why.
- Scope `Resource` values to concrete ARNs whenever the API supports it; fall back to `*` only when unavoidable and document the reason.

## Role Permissions WARNINGS and ERRORS
Treat every IAM warning or error emitted by OpenTofu or CodeBuild as a likely root cause.
- If a Lambda gains or modifies `VpcConfig`, attach the ENI permissions above to the Lambda's role unless documentation proves that an existing managed policy already covers them.
- Fix IAM policy issues before attempting to complete other parts of the change.

## Validation Checklist
- Confirm every `AWS::IAM::Role` uses a prefixed name under 64 characters.
- Ensure all `AWS::IAM::Policy` resources or `Policies` blocks target roles created in this stack and follow least-privilege rules with justification comments.
- Verify no template parameters reference external roles or ARNs.
- Fail the plan if a role omits the prefix, exceeds the length limit, or leaves permissions dangerously broad.

## Reviewer Checklist
- Change sets should include only the expected role and policy resources; unexpected replacements of unrelated infrastructure should be investigated.
- Pay special attention to IAM diffs—role replacements can trigger service disruptions if trust or attached policies change.

### Governance Decision Gate (optional)
If organizational policy forbids certain IAM changes, emit `blocked` with `category: "permissions"` and include the exact policy JSON so it can be applied out of band. Otherwise, follow this contract and keep IAM changes inside the appropriate OpenTofu stack (`opentofu/foundation/stack.tf` for foundation roles, `opentofu/application/stack.tf` for delivery roles).
