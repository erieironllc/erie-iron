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
- Any package listed in `# LAMBDA_DEPENDENCIES` **must** also be present in the base container `requirements.txt`; add it there if it is missing.
- When adding a dependency that already exists in `requirements.txt`, you must reuse the existing pinned version. Never declare conflicting package versions across lambdas or between a lambda and the base container.
- You may only use modules that are included in these packages - these are the only external modules available at runtime
- Do not assume availability of any packages that were not explicitly listed.
- Imports must work when the file and its dependencies are zipped and extracted into a flat directory structure.

---

### Execution Environment
- Do not write to local disk or depend on any files that are not bundled into the deployment package.
- All computation must be stateless unless explicitly instructed otherwise.
- Use in-memory caching or reuse of global variables only if safe and explicitly helpful.

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
