# Test Planning 

You are developing code in the **Test-Driven-Development style**.  The automated test file is located at `<test_file_path>`. 

You must follow these constraints:

- When implementing or modifying tests **Only modify <test_file_path>.** You may not create additional test files under any circumstances.
- You may modify or add tests inside `<test_file_path>`, if doing so is necessary to bring the implementation closer to the GOAL or to fix a failing assertion.
- You must preserve the original **intent and spirit** of the test logic generated in the first iteration, as defined by Test Driven Development (TDD). This means:
  - Don’t remove or neuter failing tests just to make the code pass.
  - Don’t fake inputs, mock outputs, or bypass test logic to “force” success.
  - Do not remove test assertions unless they are clearly redundant or logically invalid.
- It is acceptable to refactor or extend the test suite for clarity, coverage, or correctness — but only if it helps validate the GOAL more effectively.
- Any test edits must be fully aligned with evaluator feedback and must advance the system toward satisfying the GOAL.
- You must **never remove or replace all test logic** in the file.
  - Only append, modify, or selectively remove specific test methods that are demonstrably invalid based on evaluator diagnostics.
  - If the file structure is completely broken or non-runnable, propose a fix that **restores or scaffolds** the minimum viable test logic while preserving any intact original assertions.
  - Any full-file wipeout or replacement is considered a violation unless explicitly instructed by the evaluator.

Violating these constraints may result in invalid task execution or untrustworthy success signals, and must be avoided.

---

## Test Validation

- All core functionality must be exercised by an automated test implement in '<test_file_path>' to ensure the functionality does not regress.
- All test classes must subclass `django.test.TestCase` and include the import `from django.test import TestCase`.
- Test methods should be named `test_...` and placed in files discoverable by Django’s test runner.
- Tests must operate only on test data (not production data) and validate all critical feature behavior.
- If a test fails, it should raise an appropriate assertion error. Otherwise, it should pass silently.

---

## Real AWS Integration Test Environment
- Tests execute in an isolated AWS account and CloudFormation stack. Assume production-like conditions.
- Never use LocalStack, moto, botocore Stubber, or any AWS emulator for acceptance or smoke tests.
- Do not set `endpoint_url` on boto3 clients to non-AWS hosts. Clients must call real AWS endpoints in the region from `AWS_DEFAULT_REGION`.
- IAM permissions must be satisfied by roles defined in `infrastructure.yaml`. Create or update stack-managed roles whose `RoleName` begins with `!Ref StackIdentifier` and remains under 64 characters; update those roles when more access is required.
- Prefer long-lived infrastructure defined in the stack. Create only ephemeral data-plane resources during tests when explicitly allowed by evaluator guidance.
- Add idempotency, bounded retries with jitter, and short timeouts to accommodate eventual consistency without flakiness.

- If you need an Environment variable but it's not in the environment, you have two choices:
    1.  Create a reasonable default value (if a reasonable default exists) 
    2.  Return "Blocked" to have a human set it up (if a reasonable default does not exist)
- The stack must create whatever IAM roles it needs, prefixing each role name with the StackIdentifier value and keeping the name length at or below 64 characters.
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
