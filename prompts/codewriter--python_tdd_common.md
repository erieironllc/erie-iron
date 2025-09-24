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
- Assert externally observable outcomes: API responses, persisted records, emitted events, and log markers
- Avoid coupling to internal implementation (private functions, internal IDs not surfaced by the API)
- Expect the prompt to include existing automated tests; study them, avoid duplicating their coverage, and ensure any new tests you add can pass alongside the existing suites without contradiction.

---

## Real AWS Integration Test Environment
- Tests execute in an isolated AWS account and CloudFormation stack. Assume production-like conditions.
- Never use LocalStack, moto, botocore Stubber, or any AWS emulator for acceptance or smoke tests.
  - If a previous version of the test uses LocalStack, moto, botocore Stubber, or any AWS emulator, you **must** remove these stubs and use actual AWS resources
- Do not set `endpoint_url` on boto3 clients to non-AWS hosts. Clients must call real AWS endpoints in the region from `AWS_DEFAULT_REGION`.
- Use only the single provided IAM role via `TaskRoleArn` or the CI-assumed role. If permissions are insufficient, update the role defined by TaskRoleArn to grant the permissions; do not introduce new roles.
- Prefer long-lived infrastructure defined in the stack. Create only ephemeral data-plane resources during tests when explicitly allowed by evaluator guidance.
- Add idempotency, bounded retries with jitter, and short timeouts to accommodate eventual consistency without flakiness.

- If you need an Environment variable but it's not in the environment, you have two choices:
    1.  Create a reasonable default value (if a reasonable default exists) 
    2.  Return "Blocked" to have a human set it up (if a reasonable default does not exist)
- CloudFormation must accept a **single provided IAM role** via parameter **`TaskRoleArn`**; do **not** create additional roles.
- The `settings.py` file must **always** reside in the root of the Django application—directly alongside `manage.py`.
  - Do **not** place `settings.py` inside a subdirectory.
  - ❌ Incorrect: `"app/settings.py"`
  - ✅ Correct: `"settings.py"` 
- Include diagnostic logging in all plans.
- Minimize iteration count. Minimize file sprawl.
- Only emit blocked according to the criteria in Blocked Output Example.
- When database-related errors occur (e.g., `django.db.utils.OperationalError`, connection refused/timeouts, authentication failures), you **must** plan edits to the settings module to fully configure `DATABASES` from AWS Secrets Manager rather than escalating to a human.
- if editing settings.py, you may **must always** set the "DATABASES" variable with this line of code:  "DATABASES = agent_tools.get_django_settings_databases_conf()".  You may **never** delete this line of code
- you **may not** edit the file self_driving_coder_agent.py.  
    - if you need edits to self_driving_coder_agent.py, you must return as "Blocked"
    - only return "Blocked" in this case if you have no workarounds in the code that you are able to edit
    - if you feel you need to edit self_driving_coder_agent.py, look further at the error.  It's likely the fix is not in self_driving_coder_agent.py, rather the fix is in code that you have access to modify
