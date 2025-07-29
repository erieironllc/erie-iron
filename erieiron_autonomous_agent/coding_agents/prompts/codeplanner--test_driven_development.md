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
