## Forbidden Actions

### Testing Practices
- **never** modify, relax, skip, xfail, or delete test assertions solely to obtain a green test run. Tests are the oracle. Plan code changes to satisfy existing assertions.

### Service and Container Management
- **never** start new services or additional containers
    - Do not use docker-compose
    - All services must be defined in the existing Dockerfile. If a web service is required, use the existing Django application and configure it there
- **never** add a new container to solve application/runtime issues. If truly necessary, emit `blocked` with `category: "surface_area_expansion"` and include an approval summary in `reason`.
- **never** install OS packages to address Python‑level dependency/build issues without explicit approval; propose minimal Python‑level remedies first or emit `blocked` with `category: "surface_area_expansion"`.

### Secrets and Database Configuration
- **never** hardcode database credentials, construct secret names/paths in code, or log secret contents. Always fetch via the ARN provided in `RDS_SECRET_ARN`.
- **never** add CloudFormation parameters named `DBName` or `DBPassword`.
  - If either exists, delete it.
  - Define the database name using `StackIdentifier` plus a sensible suffix.
- **never** fall back to sqlite or any non-RDS database when RDS credentials are missing.
- **never** construct secret names or paths in code, **never** include real or placeholder secret values in plans, and **never** log secret contents. Secrets must be fetched only via the ARN provided in the designated environment variable.

### IAM and Roles
- **never** rely on out-of-stack roles.
- **never** create IAM roles whose `RoleName` fails to start with `!Ref StackIdentifier` or exceeds 64 characters.
- Inline policies must attach only to stack-defined roles, stay least privilege, and include justification comments; unbounded wildcards without explanation are forbidden.
- **never** bypass the Change Set review for IAM changes; plans must call out and reject any Role Add/Replace in the change set when the blast radius is unclear.
- **never** add test-only roles or assume roles outside the stack-managed set and the CI-assumed deployment role.

### Output and Code Paths
- **never** emit anything other than a single, well-formed JSON object as output.
  - No markdown headers, bullets, or natural-language explanations.
  - No raw code, templates, shell commands, or pseudocode.
  - No multiple sections; do not return prose plus JSON.
- **never** use absolute paths in `code_file_path`. All paths must be relative and must not start with `/`.
- **never** relocate or rename the Django settings module, and **never** change `DJANGO_SETTINGS_MODULE` as part of a plan to satisfy database configuration.

### Environment Variables and OS Packages
- **never** use decouple or similar for fetching environment variables.  Always fetch ALL environment variables directly from the os env

### AWS Service Usage
- **never** attempt to use GitHub OIDC provider or any GitHub workflows
- **never** reference or start LocalStack, moto_server, or any AWS-emulating process.
- **never** configure boto3 `endpoint_url` to `localhost`, `127.0.0.1`, or any non-AWS hostname for integration or smoke tests.
- **never** introduce botocore Stubber or request-level monkeypatching in integration or smoke tests to bypass AWS service calls.

### General Code Restrictions
- **never** edit `self_driving_coder_agent.py`. If a change seems required there and no safe workaround exists in editable files, return a blocked result.
- **never** add or edit any file inside `erieiron_common`.
- **never** plan edits to read-only or generated artifacts, including anything in `venv`, `node_modules`, `.pyc`, `.log`, or other derived/runtime-generated files.
- **never** design or deploy Lambdas that can recursively trigger themselves, directly or indirectly.

If you detect code that violates any Forbidden Action, you **must** include a concrete plan to remediate it in this iteration.
