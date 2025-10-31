

# Build Planner Prompt

The **build_planner.md** prompt receives as input a list of files modified in the current iteration and outputs a data structure describing what needs to be rebuilt.

## Purpose
The goal is to determine whether **LAMBDAS** or **CONTAINERS** need to be rebuilt based on which files have changed.

## Rules

1. **LAMBDAS**
   - Must be rebuilt if *any* Lambda code has changed.
   - Lambda code is typically found under directories such as:
     ```
     erieiron_lambda/
     lambdas/
     */lambda_*/
     ```
   - If no Lambda code was modified, set `LAMBDAS = false`.

2. **CONTAINERS**
   - Must be rebuilt if *any code other than Lambda* or OpenTofu Terraform configuration files has changed.
   - This includes:
     - Core service code (e.g., web services, agents, utilities)
     - Automated tests
     - Any shared library code
   - Containers should **not** be rebuilt if:
     - Only Lambda code changed, or
     - Only OpenTofu Terraform configurations changed (e.g., `.tf` files, related infrastructure scripts)
   - If either of the above are true, set `CONTAINERS = false`.

## Example Input

```json
{
  "modified_files": [
    "erieiron_lambda/user_auth/handler.py",
    "erieiron_common/utils.py",
    "opentofu/stacks/foundation/stack.tf"
  ]
}
```

## Example Output

```json
{
  "LAMBDAS": true,
  "CONTAINERS": true
}
```

## Logic Summary

| Change Type | Affects LAMBDAS | Affects CONTAINERS |
|--------------|-----------------|--------------------|
| Lambda code changed | ✅ | ✅ (only if non-lambda files also changed) |
| Only OpenTofu Terraform (.tf) changed | ❌ | ❌ |
| Test or non-lambda code changed | ❌ | ✅ |
| Mixed (lambda + other code) | ✅ | ✅ |

## Implementation Notes
- This prompt should return a concise JSON-like data structure (not text explanations).
- Case-insensitive matching for file paths is acceptable.
- You may assume the caller provides normalized relative paths.