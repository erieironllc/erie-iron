### Test File Planning Constraints

When a test file path is provided as `<test_file_path>`, you must follow these constraints:

- **Only modify this test file.** You may not create additional test files under any circumstances.
- You may modify or add tests inside `<test_file_path>` if doing so is necessary to bring the implementation closer to the GOAL or to fix a failing assertion.
- You must preserve the original **intent and spirit** of the test logic generated in the first iteration, as defined by Test Driven Development (TDD). This means:
  - Don’t remove or neuter failing tests just to make the code pass.
  - Don’t fake inputs, mock outputs, or bypass test logic to “force” success.
  - Do not remove test assertions unless they are clearly redundant or logically invalid.
- It is acceptable to refactor or extend the test suite for clarity, coverage, or correctness — but only if it helps validate the GOAL more effectively.
- Any test edits must be fully aligned with evaluator feedback and must advance the system toward satisfying the GOAL.

Violating these constraints may result in invalid task execution or untrustworthy success signals, and must be avoided.