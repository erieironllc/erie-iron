## Global Credential Contract (read carefully, follow exactly)

This contract defines how credentials are passed and handled across the system, ensuring security and consistency.

### Planner Requirements:
  - Credentials **must not** be hardcoded or passed as plain parameters such as "DBPassword".
  - Any credential-related parameters must be clearly documented and justified in comments.
- The codeplanner **must** define all credentials by specifying a Secrets Manager ARN variable in the correct OpenTofu module (foundation secrets stay in the foundation module; application-facing secrets belong in the application module) and wiring the ARN through tfvars.
  - The codeplanner **must** ensure that the database name is constructed using the `StackIdentifier` prefix plus a meaningful suffix; it must **not** be passed as a parameter named "DBName".
  - if editing settings.py, you may **must always** set the "DATABASES" variable with this line of code:  "DATABASES = agent_tools.get_django_settings_databases_conf()".  You may **never** delete this line of code

### OpenTofu Writer Requirements:
  - The OpenTofu modules **must** reference Secrets Manager ARNs for all secrets.
  - No plaintext secrets or passwords should appear in the templates.
  - The database name must be constructed dynamically using the stack identifier and a suffix, not passed as a variable expecting a literal name.
  - IAM policies granting access to Secrets Manager must be scoped minimally and justified with comments.

### Runtime Code Requirements:
  - Runtime code must retrieve credentials securely at runtime via the Secrets Manager ARN passed in environment variables or configuration.
  - No credentials should be stored or logged in plaintext.
  - Credential fetching logic should handle secrets refresh and errors gracefully.
  - The database connection logic must use the dynamically constructed database name based on the stack identifier.
  - Non-Django runtime code (Lambdas, background workers, CLI tools, standalone scripts) must import `get_pg8000_connection` from `erieiron_public.agent_tools` and access the database only through:
    ```python
    with get_pg8000_connection() as conn:
        conn.cursor().execute(<sql>)
    ```
    Only Django settings may invoke `agent_tools.get_django_settings_databases_conf()`; non-Django code must never call `agent_tools.get_database_conf()` directly or assemble credentials manually.

### Forbidden Actions:
This section is authoritative. All other sections must not restate these rules.
  - **Never** add parameters named "DBName" or "DBPassword" (or similar) to any configuration or infrastructure files.
  - **Never** hardcode credentials or secret values anywhere in code or configuration.
  - **Never** log or expose credentials in any logs or error messages.
  - **Never** bypass Secrets Manager or other secure credential stores.
  - **Never** set the "DATABASES" variable in settings.py with anything other than this line of code:  "DATABASES = agent_tools.get_django_settings_databases_conf()".  You may **never** delete this line of code
  - If not you notice any code or configurations that violate these rules, you must remove the offending params 

### Validation Checklist:
  - Verify that all secrets are referenced via Secrets Manager ARNs.
  - Confirm that no plaintext passwords or secrets exist in code or configuration.
  - Confirm that database names are dynamically constructed using the stack identifier.
  - Confirm IAM policies are scoped and justified for secret access.
  - Confirm runtime code retrieves credentials securely from Secrets Manager.
  - Fail if any file path or module import violates File and Module Naming rules.

### Examples:
  **Stripe API Key:**
  - Parameter in `opentofu/application/stack.tf`: `StripeSecretsArn`
  - OpenTofu grants access to the secret ARN for the relevant Lambda functions
  - Runtime code fetches the Stripe API key at invocation time securely by fetching the Strip API key secret via the key defined in StripeSecretsArn 
