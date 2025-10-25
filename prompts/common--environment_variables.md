## Environment Variables

The following environment variables are passed in the environment and are accessable by all code:
<env_vars>

### Environment Variable Rules
- The variables listed above are the only environment variables your code is allowed to read.
- Your code must not read environment variables that are not listed above.
- If code references an undeclared environment variable, treat it as a known compile-time error: fail fast during import/settings load with a single-line message describing the missing/disallowed variable and the fix. Do not wait for a later test failure.

### Conventions and aliases
- Region - use `AWS_DEFAULT_REGION`. If `AWS_REGION` is present, treat it as an alias; do not require it.
- Non-Django database helpers must rely on `get_pg8000_connection()` from `erieiron_public.agent_tools`, which reads the active region (`AWS_DEFAULT_REGION` or the approved alias) internally; never pass raw credentials into `agent_tools.get_database_conf()` yourself.
- System variables - `PATH` may be present for process execution but must not be used for application logic.
- Host credentials - the system may read `AWS_PROFILE` on the host solely to obtain credentials; application code and tests must not rely on it.

### Enforcement
- When you encounter code that references an undeclared environment variable:
  1. Remove the reference.
  2. Refactor to use an existing variable or a settings/config constant.
  3. If a new variable is truly required, return a "Blocked" response
- Do not silently default to localhost or in-container services when an expected variable is missing - fail fast as above.
  

## Missing-Env-Var Rule For Cloud Resources
When errors include missing environment variables that map to cloud resources
(examples: EMAIL_INGEST_S3_BUCKET, STORAGE_BUCKET, EMAIL_STORAGE_BUCKET, DB_HOST, QUEUE_URL), DO NOT add code defaults or .env fallbacks.

Instead:
1) Update CloudFormation to create the resource if it does not exist (or reference an existing exported value).
2) Inject the resource identifier into the service’s Environment variables.
3) Attach the minimum IAM permissions the service needs for that resource.
4) Re-run deployment, then re-run tests.

**This rule supersedes any guidance to add local defaults for production flows.**
