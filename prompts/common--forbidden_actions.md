## Forbidden Actions

### Testing Practices
- Never modify, relax, skip, xfail, or delete test assertions solely to obtain a green test run. Tests are the oracle. Plan code changes to satisfy existing assertions.

### Service and Container Management
- Never start new services or additional containers
    - Do not use docker-compose
    - All services must be defined in the existing Dockerfile. If a web service is required, use the existing Django application and configure it there
- Never add a new container to solve application/runtime issues. If truly necessary, emit `blocked` with `category: "surface_area_expansion"` and include an approval summary in `reason`.
- Never install OS packages to address Python‑level dependency/build issues without explicit approval; propose minimal Python‑level remedies first or emit `blocked` with `category: "surface_area_expansion"`.

### Secrets and Database Configuration
- Never hardcode database credentials, construct secret names/paths in code, or log secret contents. Always fetch via the ARN provided in `RDS_SECRET_ARN`.
- Never add CloudFormation parameters named `DBName` or `DBPassword`.
  - If either exists, delete it.
  - Define the database name using `StackIdentifier` plus a sensible suffix.
- Never fall back to sqlite or any non-RDS database when RDS credentials are missing.
- Never construct secret names or paths in code, never include real or placeholder secret values in plans, and never log secret contents. Secrets must be fetched only via the ARN provided in the designated environment variable.

### IAM and Roles
- Never add CloudFormation resources of type `AWS::IAM::Role`, `AWS::IAM::InstanceProfile`, or `AWS::IAM::Policy` to create or attach new roles within the stack. All role usage must reference the provided `TaskRoleArn`.
- Never introduce parameters intended to generate or select additional roles (e.g., `ExistingTaskRoleArn`, `ExecutionRoleArn`, `CreateTaskRole`). Erie Iron always passes a single role via `TaskRoleArn`.
- Never bypass the Change Set review for IAM changes; plans must call out and reject any Role Add/Replace in the change set.
- Never add test-only roles or assume roles other than the single provided `TaskRoleArn` or CI-assumed role.

### Output and Code Paths
- Never emit anything other than a single, well-formed JSON object as output.
  - No markdown headers, bullets, or natural-language explanations.
  - No raw code, templates, shell commands, or pseudocode.
  - No multiple sections; do not return prose plus JSON.
- Never use absolute paths in `code_file_path`. All paths must be relative and must not start with `/`.
- Never relocate or rename the Django settings module, and never change `DJANGO_SETTINGS_MODULE` as part of a plan to satisfy database configuration.

### Environment Variables and OS Packages
- Never use decouple or similar for fetching environment variables.  Always fetch ALL environment variables directly from the os env

### AWS Service Usage
- Never attempt to use GitHub OIDC provider or any GitHub workflows
- Never reference or start LocalStack, moto_server, or any AWS-emulating process.
- Never configure boto3 `endpoint_url` to `localhost`, `127.0.0.1`, or any non-AWS hostname for integration or smoke tests.
- Never introduce botocore Stubber or request-level monkeypatching in integration or smoke tests to bypass AWS service calls.

### General Code Restrictions
- Never edit `self_driving_coder_agent.py`. If a change seems required there and no safe workaround exists in editable files, return a blocked result.
- Never add or edit any file inside `erieiron_common`.
- Never plan edits to read-only or generated artifacts, including anything in `venv`, `node_modules`, `.pyc`, `.log`, or other derived/runtime-generated files.
- Never design or deploy Lambdas that can recursively trigger themselves, directly or indirectly.

If you detect code that violates any Forbidden Action, you **must** include a concrete plan to remediate it in this iteration.
