## IAM Role Usage Contract 

The system enforces a single-role model per {business, env}. The role is **constructed outside** CloudFormation and is **always** provided to the template as the required parameter `TaskRoleArn`.

**Required wiring**
- ECS task definitions:
  - `TaskRoleArn: !Ref TaskRoleArn`
  - `ExecutionRoleArn: !Ref TaskRoleArn`
- Lambda functions:
  - `Role: !Ref TaskRoleArn`
- Any other service fields that accept a role ARN must also reference `!Ref TaskRoleArn`.

**Prohibited**
- Do not add resources of type `AWS::IAM::Role`, `AWS::IAM::InstanceProfile`, or `AWS::IAM::Policy`.
- Do not add parameters that select or generate additional roles.
- Do not add 'AllowedPattern' to the 'TaskRoleArn' parameter definition.
    - If the existing 'TaskRoleArn' parameter definition has an 'AllowedPattern' value, **you must** remove it

**Assumptions**
- Trust and permissions for the provided role are managed outside this template. Do not attempt to modify them here.

---

## Role Permissions WARNINGS and ERRORS
Consider any AWS role or permissions WARNING or ERROR messages as likely root cause errors.  (Even if it's just a reported as a warning)
- **you must** prioritize fixing these role permissions before attempting other changes