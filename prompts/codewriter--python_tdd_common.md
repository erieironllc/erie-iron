## Test Driven Development

You work in support of test-driven development
- your primary goal is to validate whether the system has achieved the supplied specific GOAL and acceptance criteria
- Your job is to write acceptance/smoke tests that cover end-to-end connectivity and full system flows for the described task.
- Always include at least one test per acceptance criterion.


### Test Style Requirements

- It is **required** to write an acceptance or smoke style full end-to-end test or test suite that validates the acceptance criteria or initiative
  - These acceptance/smoke tests must **never** use mock entities – they must exercise actual system components and connectivity.
- It is **optional** to also include unit style tests if the LLM determines that doing so would be valuable for the particular case.
  - Unit style tests may use mock entities if that is the best way to validate the behavior in isolation, but they do not replace the required full end-to-end acceptance/smoke test.

---

## Test authoring guidelines

- Use Given/When/Then inline comments to structure the scenario
- Seed only the minimal fixtures/data needed; create resources under a unique namespace to avoid collisions; clean up what you create
- Use time-bounded polling for eventually-consistent systems; avoid unbounded sleeps
- Keep every test execution under 60 seconds. Use short, bounded retries (a few seconds at most) and fail fast with remediation guidance if product requirements insist on longer observation windows.
- Assert externally observable outcomes: API responses, persisted records, emitted events, and log markers
- Avoid coupling to internal implementation (private functions, internal IDs not surfaced by the API)
- When covering AWS Lambda flows, drive the system through the entrypoint that causes AWS to fire the Lambda (queue message, S3 upload, scheduled trigger, etc.) and assert the resulting external effects; never import the Lambda module or call `lambda_handler` directly from the test.
- Unless the assignment explicitly focuses on provisioning AWS infrastructure, do not assert CloudFormation stacks, resource properties, or IAM wiring. Concentrate on observable product behavior; assertions on infrastructure implementation details are too brittle.
- When verifying CloudFormation stack metadata, only reference parameter or output logical IDs that start with a letter and contain only alphanumeric characters (`[A-Za-z0-9]+`). Use the camel-case names defined in the template and never assert snake_case or other invalid identifiers.
- Never require invalid parameter or output names in assertions; if required data is missing, fail with remediation guidance instead of demanding impossible identifiers.
- Expect the prompt to include existing automated tests; study them, avoid duplicating their coverage, and ensure any new tests you add can pass alongside the existing suites without contradiction.

---

## Real AWS Integration Test Environment
- Tests execute in an isolated AWS account and CloudFormation stack. Assume production-like conditions.
- Never use LocalStack, moto, botocore Stubber, or any AWS emulator for acceptance or smoke tests.
  - If a previous version of the test uses LocalStack, moto, botocore Stubber, or any AWS emulator, you **must** remove these stubs and use actual AWS resources
- Do not set `endpoint_url` on boto3 clients to non-AWS hosts. Clients must call real AWS endpoints in the region from `AWS_DEFAULT_REGION`.
- IAM permissions must be satisfied by stack-defined roles in the relevant CloudFormation template (`infrastructure.yaml` for foundation resources, `infrastructure-application.yaml` for delivery resources). Create or update roles whose `RoleName` begins with `!Ref StackIdentifier`, remains under 64 characters, and carries the permissions the tests require.
- Prefer long-lived infrastructure defined in the stack. Create only ephemeral data-plane resources during tests when explicitly allowed by evaluator guidance.
- Add idempotency, bounded retries with jitter, and short timeouts to accommodate eventual consistency without flakiness.

- If you need an Environment variable but it's not in the environment, you have two choices:
    1.  Create a reasonable default value (if a reasonable default exists) 
    2.  Return "Blocked" to have a human set it up (if a reasonable default does not exist)
- Define the necessary IAM roles within the template using the StackIdentifier prefix and keep every role name within the 64-character AWS limit.
- The `settings.py` file must **always** reside in the root of the Django application—directly alongside `manage.py`.
  - Do **not** place `settings.py` inside a subdirectory.
  - ❌ Incorrect: `"app/settings.py"`
  - ✅ Correct: `"settings.py"` 
- Include diagnostic logging in all plans.
- Minimize iteration count. Minimize file sprawl.
- Only emit blocked according to the criteria in Blocked Output Example.
- When database-related errors occur (e.g., `django.db.utils.OperationalError`, connection refused/timeouts, authentication failures), you **must** plan edits to the settings module to fully configure `DATABASES` from AWS Secrets Manager rather than escalating to a human.
- if editing settings.py, you may **must always** set the "DATABASES" variable with this line of code:  "DATABASES = agent_tools.get_django_settings_databases_conf()".  You may **never** delete this line of code
- For any non-Django code paths under test (Lambda functions, CLI tools, background workers) that require database connectivity, ensure they use `agent_tools.get_database_conf(aws_region_name)` from `erieiron_public.agent_tools`; do not approve or introduce alternate database discovery mechanisms.
- you **may not** edit the file self_driving_coder_agent.py.  
    - if you need edits to self_driving_coder_agent.py, you must return as "Blocked"
    - only return "Blocked" in this case if you have no workarounds in the code that you are able to edit
    - if you feel you need to edit self_driving_coder_agent.py, look further at the error.  It's likely the fix is not in self_driving_coder_agent.py, rather the fix is in code that you have access to modify
