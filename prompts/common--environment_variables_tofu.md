## Environment Variables

The Erie Iron platform exposes a fixed base set of environment variables plus any iteration-specific entries enumerated in `<env_vars>` (rendered by the evaluator). Code **may only read** variables from those two sources.

### OpenTofu Output to Environment Variable Translation

**How OpenTofu Outputs Become Environment Variables:**

OpenTofu stack outputs (defined in `opentofu/application/stack.tf` and `opentofu/target_account_provisioning/stack.tf`) use CamelCase naming (e.g., `LoadBalancerDNSName`, `RdsInstanceEndpoint`, `ApplicationUrl`). These are automatically converted to ALL_CAPS_SNAKE_CASE environment variables for use in runtime code and tests.

**Automatic Conversion:**
- `LoadBalancerDNSName` → `LOAD_BALANCER_DNS_NAME`
- `RdsInstanceEndpoint` → `RDS_INSTANCE_ENDPOINT`
- `ApplicationUrl` → `APPLICATION_URL`
- `EcsClusterName` → `ECS_CLUSTER_NAME`
- `RdsInstanceDBName` → `RDS_INSTANCE_DB_NAME`

**Override Registry (ENVVAR_TO_STACK_OUTPUT):**

Some outputs use explicit env var names defined defined by this mapping:

```python
ENVVAR_TO_STACK_OUTPUT = {
    'AWS_REGION': 'AwsRegion',
    'AWS_DEFAULT_REGION': 'AwsRegion',
    'ERIEIRON_DB_NAME': 'RdsInstanceDBName',
    'ERIEIRON_DB_HOST': 'RdsInstanceEndpoint',
    'ERIEIRON_DB_PORT': 'RdsInstancePort'
}
```

When an output name appears as a VALUE in this registry, the corresponding KEY becomes the env var name in addition to the auto-generated name.


### Canonical base set (always allowed)
| Name | Purpose / Notes | Source |
| --- | --- | --- |
| `AWS_DEFAULT_REGION` | Primary AWS region for the stack. Pass to SDK clients; never hardcode regions. | Operator runtime |
| `AWS_REGION` | Alias for `AWS_DEFAULT_REGION`. Treat as optional; do not depend on it exclusively. | Operator runtime |
| `STACK_IDENTIFIER` | Task-specific namespace used for naming AWS resources. Mirrors the OpenTofu `StackIdentifier` parameter and already respects service length limits. | Application stack |
| `FOUNDATION_STACK_IDENTIFIER` | Legacy namespace (now same as `STACK_IDENTIFIER`). Maintained for backward compatibility. | Application stack |
| `TASK_NAMESPACE` | Legacy alias for `STACK_IDENTIFIER`. Use only when existing helpers require it; prefer `STACK_IDENTIFIER` in new code. | Orchestration |
| `DOMAIN_NAME` | Fully-qualified subdomain assigned to this task. Build URLs, email addresses, and docs from this value instead of literals. | Orchestration |
| `RDS_SECRET_ARN` | ARN of the Secrets Manager secret created by `ManageMasterUserPassword: true`. Pass into helpers; never construct secret names manually. | Application stack |
| `ERIEIRON_DB_NAME` | Logical database name (typically `appdb`). Required alongside the secret ARN. | Application stack |
| `ERIEIRON_DB_HOST` | RDS endpoint address. | Application stack |
| `ERIEIRON_DB_PORT` | Database port (from the RDS endpoint). Do **not** hardcode 5432. | Application stack |

### Additional variables supplied per iteration
The evaluator may append extra entries (e.g., `MESSAGE_BUS_TOPIC`, `STORAGE_BUCKET`, `LLM_PROVIDER`). Those values will appear directly beneath this section as rendered `<env_vars>`. They are permitted for the current iteration only. Any variable absent from both the table above and the rendered list is disallowed.

### Environment Variable Rules
- Reads must be limited to the canonical table plus the rendered `<env_vars>` list.
- If code references an undeclared environment variable, treat that as a compile-time error: fail fast during import/settings load with a single-line message that names the missing variable and next steps. Do not wait for later tests to fail.
- To request a brand-new variable, emit `{ "blocked": { "category": "env_contract", "reason": "<why the new variable is required>" } }` instead of inventing defaults.

<env_vars>

### Conventions and aliases
- Region – prefer `AWS_DEFAULT_REGION`. If `AWS_REGION` is present, treat it as a secondary alias; do not require it.
- Database helpers – non-Django runtimes must call `get_pg8000_connection()` from `erieiron_public.agent_tools`, which already consumes the approved env vars. Never parse the secret ARN manually or construct connection strings by hand.
- System variables – process-level settings such as `PATH` may exist but must never drive business logic.
- Host credentials – the launcher may read `AWS_PROFILE` to obtain credentials; code inside the repo must not depend on it.

### Enforcement
1. Remove any undeclared env-var reads you encounter.
2. Refactor the logic to consume an approved variable or constant.
3. If no approved variable fits, block the task as described above so the operator can add it explicitly.
- Do **not** introduce `.env` files or hardcoded fallbacks such as `localhost` placeholders when a required variable is missing.


## Missing-Env-Var Rule For Cloud Resources
When errors include missing environment variables that map to cloud resources
(examples: EMAIL_INGEST_S3_BUCKET, STORAGE_BUCKET, EMAIL_STORAGE_BUCKET, DB_HOST, QUEUE_URL), DO NOT add code defaults or .env fallbacks.

Instead:
1) Update OpenTofu to create the resource if it does not exist (or reference an existing exported value).
2) Inject the resource identifier into the service’s Environment variables.
3) Attach the minimum IAM permissions the service needs for that resource.
4) Re-run deployment, then re-run tests.

**This rule supersedes any guidance to add local defaults for production flows.**
