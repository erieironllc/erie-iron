# Test Planning 

You are developing code in the **Test-Driven-Development style**.  The automated test file is located at `<test_file_path>`. 

## Constraints

- When tests need to send emails, always construct the "FROM" address from the task's DomainName (for example, `noreply@{os.getenv("DOMAIN_NAME")}`) and ensure that identity is verified in SES before the test runs.
- Automated SES smoke or integration tests must target the SES Mailbox Simulator (e.g., `success@simulator.amazonses.com` for happy-path delivery, or other simulator addresses when validating bounces/complaints). Never point tests at real recipients or DomainName inboxes.
- When implementing or modifying tests **Only modify <test_file_path>.** You may not create additional test files under any circumstances.
- If repeated iterations show the test itself is faulty or lacks the logging needed to diagnose failures, update it to correct the issue or add focused diagnostics while keeping the test’s spirit intact.
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
- Tests should validate end-user observable behavior directly wherever feasible (for example, by sending a request to an endpoint and asserting the response).  
- Assertions based on logs or internal messages should be used only as a **last resort**, when no direct or reliable method exists to confirm expected end behavior.  
- Prefer validating functional outcomes through API responses, database state, or returned data rather than by checking for log text.
- All test classes must subclass `django.test.TestCase` and include the import `from django.test import TestCase`.
- Test methods should be named `test_...` and placed in files discoverable by Django’s test runner.
- Tests must operate only on test data (not production data) and validate all critical feature behavior.
- If a test fails, it should raise an appropriate assertion error. Otherwise, it should pass silently.

---

## Integration and Environment Strategy

- Tests must validate that the full workflow **really works in the deployed stack**. They should assert functional success end-to-end rather than rely on mocked dependencies or isolated components.
- AWS and other integrations should be exercised through the actual deployed environment (for example, real S3 uploads, SES email sends, or RDS queries)
- Tests **must avoid** asserting internal configuration details such as role names, stack identifiers, or region values.
- The objective is to prove that the system’s observable outcomes are correct in a real environment — e.g., data is stored, events are processed, and external effects occur as expected.
- Mocking or stubbing of AWS services is **not allowed**; tests must run against real or stack-provisioned resources that mirror production conditions.
- When instability or eventual consistency may cause flakiness, tests should include bounded retries with jitter and clear diagnostics rather than disabling validation.
- Logging and diagnostics should focus on visibility of functional flow (what succeeded or failed), not on enforcing static configuration expectations.
