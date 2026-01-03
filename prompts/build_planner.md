# Build Planner Prompt

Analyzes modified files to determine build, test, and deployment strategy.

## Input
List of files modified in the current iteration.

## Output
JSON object with boolean flags for each build/test/deploy decision.

## Decision Logic

### 1. BUILD_LAMBDAS
- **True** if any Lambda code modified
- Lambda paths: `erieiron_lambda/`, `lambdas/`, `*/lambda_*/`
- **False** otherwise

### 2. BUILD_CONTAINER
- **True** if container build is needed:
  - Any application code changed (Python files, frontend code, etc.)
  - Dockerfile changed
  - Dependencies changed (requirements.txt, package.json)
- **False** if only .tf files, docs, or test files changed (and DEPLOY_TO_AWS is false)

### 3. RUN_TESTS_LOCALLY
- **True** if ONLY the following changed:
  - Python files (*.py) excluding models.py
  - Test files (*test*.py, tests/)
  - No Terraform files (.tf)
  - No Dockerfile changes
- **False** if stack.tf, models.py, or Dockerfile changed

### 4. RUN_TESTS_IN_CONTAINER
- **True** if:
  - BUILD_CONTAINER is true AND
  - (Dockerfile changed OR first iteration after local tests pass)
- **False** if only Python/test changes

### 5. DEPLOY_TO_AWS
- **True** if any of:
  - stack.tf or any .tf files changed
  - models.py changed (requires DB migration in deployed environment)
  - Container tests passed (transition from CONTAINER_TESTS mode)
- **False** if only local code/test changes

### 6. MIGRATE_DATABASE
- **True** if:
  - models.py modified
  - Any file in migrations/ directory modified
- **False** otherwise

### 7. UPDATE_STACK_TF
- **True** if any .tf file modified (stack.tf, variables.tf, outputs.tf, etc.)
- **False** otherwise

### 8. PUSH_TO_ECR
- **True** if ECR push is needed:
  - Application code or Dockerfile changed
  - Container needs to be deployed to AWS
- **False** if only test files, .tf files, or docs changed

### 9. RUN_BACKEND_TESTS
- **True** if backend tests should be executed:
  - Any backend code changed (*.py files)
  - Backend dependencies changed
- **False** if only frontend files or .tf files changed

### 10. RUN_FRONTEND_TESTS
- **True** if frontend tests should be executed:
  - Any frontend code changed (*.js, *.scss, templates/)
  - Frontend dependencies changed
- **False** if only backend Python files or .tf files changed

### 11. REASONING
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
  "BUILD_LAMBDAS": false,
  "BUILD_CONTAINER": false,
  "RUN_TESTS_LOCALLY": true,
  "RUN_TESTS_IN_CONTAINER": false,
  "DEPLOY_TO_AWS": false,
  "MIGRATE_DATABASE": false,
  "UPDATE_STACK_TF": false,
  "PUSH_TO_ECR": false,
  "RUN_BACKEND_TESTS": true,
  "RUN_FRONTEND_TESTS": false,
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
  "BUILD_LAMBDAS": false,
  "BUILD_CONTAINER": true,
  "RUN_TESTS_LOCALLY": false,
  "RUN_TESTS_IN_CONTAINER": true,
  "DEPLOY_TO_AWS": true,
  "MIGRATE_DATABASE": true,
  "UPDATE_STACK_TF": false,
  "PUSH_TO_ECR": true,
  "RUN_BACKEND_TESTS": true,
  "RUN_FRONTEND_TESTS": false,
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
  "BUILD_LAMBDAS": false,
  "BUILD_CONTAINER": false,
  "RUN_TESTS_LOCALLY": false,
  "RUN_TESTS_IN_CONTAINER": false,
  "DEPLOY_TO_AWS": true,
  "MIGRATE_DATABASE": false,
  "UPDATE_STACK_TF": true,
  "PUSH_TO_ECR": false,
  "RUN_BACKEND_TESTS": true,
  "RUN_FRONTEND_TESTS": false,
  "REASONING": "Stack.tf modified. Must deploy to AWS to apply infrastructure changes. Container unchanged so no rebuild or ECR push needed."
}
```

## Example 4: Frontend Only Change

**Input:**
```json
{
  "modified_files": [
    "myapp/static/js/app.js",
    "myapp/templates/dashboard.html"
  ]
}
```

**Output:**
```json
{
  "BUILD_LAMBDAS": false,
  "BUILD_CONTAINER": true,
  "RUN_TESTS_LOCALLY": false,
  "RUN_TESTS_IN_CONTAINER": true,
  "DEPLOY_TO_AWS": true,
  "MIGRATE_DATABASE": false,
  "UPDATE_STACK_TF": false,
  "PUSH_TO_ECR": true,
  "RUN_BACKEND_TESTS": false,
  "RUN_FRONTEND_TESTS": true,
  "REASONING": "Only frontend files changed. Need container build and deploy for template changes but can skip backend tests."
}
```

## Example 5: Documentation Only Change

**Input:**
```json
{
  "modified_files": [
    "README.md",
    "docs/architecture.md"
  ]
}
```

**Output:**
```json
{
  "BUILD_LAMBDAS": false,
  "BUILD_CONTAINER": false,
  "RUN_TESTS_LOCALLY": false,
  "RUN_TESTS_IN_CONTAINER": false,
  "DEPLOY_TO_AWS": false,
  "MIGRATE_DATABASE": false,
  "UPDATE_STACK_TF": false,
  "PUSH_TO_ECR": false,
  "RUN_BACKEND_TESTS": false,
  "RUN_FRONTEND_TESTS": false,
  "REASONING": "Only documentation changed. No build, test, or deployment needed."
}
```

## Implementation Notes
- Return only the JSON structure, no additional explanation
- Be conservative: when uncertain, choose the safer (more complete) build path
- Consider file paths case-insensitively
- Execution control flags enable fine-grained optimization while maintaining safety
