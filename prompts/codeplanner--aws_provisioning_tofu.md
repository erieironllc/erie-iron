## OpenTofu Provisioning Planner Expectations

When drafting a plan that touches infrastructure:

- Identify which OpenTofu module(s) require changes (foundation vs application). Limit the scope to only the necessary files under `opentofu/`.
- Call out new or modified variables, outputs, and resources. Specify how values will flow from tfvars into modules and back out to application code.
- Highlight safety checks: required `tofu fmt`, `tofu validate`, and plan review before apply. Note any drift detection or workspace rotation steps.
- Enumerate AWS resources affected (ECS, RDS, IAM, Route53, etc.) and the blast radius for each. Include rollback considerations when resources are replaced.
- Document secret handling. Secrets belong in AWS Secrets Manager or tfvars (referencing an ARN), never in the module source.
- Note dependencies on existing outputs so reviewers understand integration points. If a new output is required, mention who consumes it.
- Capture follow-up actions for operators (e.g., SES verification) when automation cannot finish the task.
- If the plan introduces policies or permissions, justify the access scope and reference the roles that will receive it.

Plans must remain actionable, appropriately scoped, and reviewable without running `tofu plan`.
