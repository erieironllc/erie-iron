## Special Instructions for AWS Lambda functions
When writing or modifying a Lambda, these rules **must** be followed.  If you discover a lambda that does not comport to these rules, it must be fixed

### Handler Function
- You must define exactly one entrypoint function named `lambda_handler`.
- The handler must use the AWS Lambda signature:
  ```python
  def lambda_handler(event, context):
  ```
- This function will be called by the AWS Lambda runtime. Do not rename or overload it.

---

### Dependency Awareness
- The following pypi packages are available at runtime:
<included_dependencies>

- At the top of the file, **you must** include a structured header comment that lists all the required PyPI packages for this Lambda. This header will be parsed at deploy time to install dependencies.
- Format:
  ```python
  # LAMBDA_DEPENDENCIES: ["requests", "boto3"]
  ```
- The list must match exactly the modules actually imported in the file. If no external dependencies are required, include an empty list:
  ```python
  # LAMBDA_DEPENDENCIES: []
  ```
- When adding a dependency that already exists in `requirements.txt`, you must reuse the existing pinned version. Never declare conflicting package versions across lambdas or between a lambda and the base container.
- You may only use modules that are included in these packages - these are the only external modules available at runtime
- Imports must work when the file and its dependencies are zipped and extracted into a flat directory structure.

- To proactively enforce correct dependency declarations, the codewriter **must** automatically detect all imported modules and update the `LAMBDA_DEPENDENCIES` header comment accordingly before writing or modifying code.  
  - If a new module is imported in the code but not present in the dependency list, the codewriter must immediately add it to the `LAMBDA_DEPENDENCIES` header.
  - Deployment and runtime validation processes will enforce this requirement strictly; builds will fail if inconsistencies between imports and declared dependencies are found.
  - Example workflow:
    - Before adding a new import:
      ```python
      # LAMBDA_DEPENDENCIES: ["requests"]
      import requests
      ```
    - After adding a new import of `boto3`:
      ```python
      # LAMBDA_DEPENDENCIES: ["requests", "boto3"]
      import requests
      import boto3
      ```
- Import-error concretes (**binding rule**): When a Lambda fails with `Runtime.ImportModuleError` for a known package, resolve it by declaring the exact dependency in `LAMBDA_DEPENDENCIES`. For example, if logs show `No module named 'psycopg2._psycopg'`, declare `# LAMBDA_DEPENDENCIES: ["psycopg2-binary", ...]`. Do not introduce retries or refactors until the import succeeds.

---

### Execution Environment
- Do not write to local disk or depend on any files that are not bundled into the deployment package.
- All computation must be stateless unless explicitly instructed otherwise.
- Use in-memory caching or reuse of global variables only if safe and explicitly helpful.

### Database Connectivity
- If the Lambda requires database access, you **must** import `get_pg8000_connection` from `erieiron_public.agent_tools` and execute queries via:
  ```python
  from erieiron_public.agent_tools import get_pg8000_connection

  with get_pg8000_connection() as conn:
      conn.cursor().execute(<sql>)
  ```
  Do not reconstruct credentials, read raw environment variables for connection pieces, or query Secrets Manager directly.
- Only the shared helper may be used to obtain connection details. Code running in Django continues to rely on Django settings helpers for `DATABASES` configuration—do not duplicate that logic inside Lambda code.

---

### Logging
- Use `print()` for logging. These will be captured by AWS CloudWatch.
- At a minimum, log:
  - Function start
  - Important fields from the incoming `event`
  - Key decisions or conditionals
  - Any errors or unexpected branches

---

### Retry & Idempotency Awareness
- Lambda functions may be retried automatically. Design your logic to be safe to execute more than once unless explicitly told otherwise.

---

### SES Receipt Rule Set Delete Handling
Insert a step in the deletion handler:
- On `DELETE`, send a request with `"RuleSetName": ""` to SES before attempting to remove the resource.
- If an error `"RuleSetName is required"` is returned, retry with empty string.
