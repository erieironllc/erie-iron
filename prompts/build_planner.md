# Build Planner Prompt

Analyzes modified files to determine build, test, and deployment strategy.

## Input
List of files modified in the current iteration.

## Output
JSON object with boolean flags for each build/test/deploy decision.

## Decision Logic

### 1. LAMBDAS
- **True** if any Lambda code modified
- Lambda paths: `erieiron_lambda/`, `lambdas/`, `*/lambda_*/`
- **False** otherwise

### 2. CONTAINERS
- **True** if any non-Lambda, non-Terraform code modified
- Includes: Python files, tests, shared libraries, frontend code
- **False** if only Lambda or Terraform files changed

### 3. RUN_TESTS_LOCALLY
- **True** if ONLY the following changed:
  - Python files (*.py) excluding models.py
  - Test files (*test*.py, tests/)
  - No Terraform files (.tf)
  - No Dockerfile changes
- **False** if stack.tf, models.py, or Dockerfile changed

### 4. RUN_TESTS_IN_CONTAINER
- **True** if:
  - CONTAINERS is true AND
  - (Dockerfile changed OR first iteration after local tests pass)
- **False** if only Python/test changes

### 5. DEPLOY_TO_AWS
- **True** if any of:
  - stack.tf or any .tf files changed
  - models.py changed (requires DB migration in deployed environment)
  - Container tests passed (transition from CONTAINER_TESTS mode)
- **False** if only local code/test changes

### 6. DB_MIGRATION_REQUIRED
- **True** if:
  - models.py modified
  - Any file in migrations/ directory modified
- **False** otherwise

### 7. STACK_TF_CHANGED
- **True** if any .tf file modified (stack.tf, variables.tf, outputs.tf, etc.)
- **False** otherwise

### 8. REASONING
- 1-2 sentence explanation of the decision
- Example: "Only test files changed, can iterate locally. No deployment needed."

## Example 1: Simple Code Change

**Input:**
```json
{
  "modified_files": [
    "myapp/views.py",
    "myapp/tests/test_views.py"
  ]
}
```

**Output:**
```json
{
  "LAMBDAS": false,
  "CONTAINERS": true,
  "RUN_TESTS_LOCALLY": true,
  "RUN_TESTS_IN_CONTAINER": false,
  "DEPLOY_TO_AWS": false,
  "DB_MIGRATION_REQUIRED": false,
  "STACK_TF_CHANGED": false,
  "REASONING": "Only Python code and tests changed. Can iterate locally without container build or deployment."
}
```

## Example 2: Model Change

**Input:**
```json
{
  "modified_files": [
    "myapp/models.py",
    "myapp/tests/test_models.py"
  ]
}
```

**Output:**
```json
{
  "LAMBDAS": false,
  "CONTAINERS": true,
  "RUN_TESTS_LOCALLY": false,
  "RUN_TESTS_IN_CONTAINER": true,
  "DEPLOY_TO_AWS": true,
  "DB_MIGRATION_REQUIRED": true,
  "STACK_TF_CHANGED": false,
  "REASONING": "Models changed, requiring DB migration. Must build container and deploy to AWS to test migration."
}
```

## Example 3: Stack Change

**Input:**
```json
{
  "modified_files": [
    "opentofu/stacks/application/stack.tf",
    "myapp/views.py"
  ]
}
```

**Output:**
```json
{
  "LAMBDAS": false,
  "CONTAINERS": true,
  "RUN_TESTS_LOCALLY": false,
  "RUN_TESTS_IN_CONTAINER": true,
  "DEPLOY_TO_AWS": true,
  "DB_MIGRATION_REQUIRED": false,
  "STACK_TF_CHANGED": true,
  "REASONING": "Stack.tf modified. Must deploy to AWS to apply infrastructure changes."
}
```

## Implementation Notes
- Return only the JSON structure, no additional explanation
- Be conservative: when uncertain, choose the safer (more complete) build path
- Consider file paths case-insensitively
