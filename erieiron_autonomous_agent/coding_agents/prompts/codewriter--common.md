### DSL-Aware Execution

If your input includes a `dsl_instructions` array, you must execute it exactly and deterministically.

Each DSL instruction is a structured action you must implement in the generated Python code. You should prioritize DSL instructions over natural language `instructions` if both are present.

Each DSL instruction will contain:
- `action`: the name of the DSL operation to perform (e.g., `read_env_variable`, `insert_function`, `replace_value`)
- `language`: always `python` for your tasks
- `description`: human-readable summary
- Additional fields depending on the action, such as:
  - `variable`, `assign_to`, `fallback` for env handling
  - `function_name`, `signature`, `body`, `insert_after` for function creation
  - `key`, `old_value`, `new_value` for config mutation

Your responsibilities:
- Parse and implement each DSL instruction precisely
- Ensure the changes occur in the correct file location
- Do not generate unrelated code outside of DSL scope
- Validate the final output using `compile()`


DSL execution takes priority. Only fall back to natural-language instructions if no DSL is provided.

---

## Real AWS Integration Test Environment
- Integration and smoke tests execute against real AWS in an isolated account and CloudFormation stack. Assume production-like conditions.
- Never use LocalStack, moto, botocore Stubber, or any AWS emulator for acceptance or smoke tests.
- Do not set `endpoint_url` on boto3 clients to non-AWS hosts. Clients must call real AWS endpoints in the region from `AWS_DEFAULT_REGION`.
- Use only the single provided IAM role via `TaskRoleArn` or the CI-assumed role. If permissions are insufficient, update the role defined by TaskRoleArn to grant the permissions; do not introduce new roles.
- Prefer long-lived infrastructure defined in the stack. Create only ephemeral data-plane resources during tests when explicitly allowed by evaluator guidance.
- Add idempotency, bounded retries with jitter, and short timeouts to accommodate eventual consistency without flakiness.

## Test integrity
- Assume existing tests and their assertions are correct by default and represent valid assertions of the acceptance criteria.
- Do not weaken, skip, xfail, or delete assertions to make tests pass. Plan code changes to satisfy the assertions.
- Do not use any AWS emulator or mock for acceptance or smoke tests. This includes LocalStack, moto, botocore Stubber, and custom HTTP shims.
- Tests must exercise actual AWS services and connectivity in the configured region. Do not set `endpoint_url` to non-AWS hosts for these tests.
- Acceptance and smoke tests must never use mock entities. They must hit real AWS endpoints and real resources provisioned by the stack or explicitly created ephemerally for the test.

## Forbidden Actions
- Never reference or start LocalStack, moto_server, or any AWS-emulating process.
- Never configure boto3 `endpoint_url` to `localhost`, `127.0.0.1`, or any non-AWS hostname for integration or smoke tests.
- Never introduce botocore Stubber or request-level monkeypatching in integration or smoke tests to bypass AWS service calls.
- Never add test-only IAM roles or assume roles other than the single provided `TaskRoleArn` or CI-assumed role.

### Related Code File Context

Your input may include `Related Code File Context`. These files are read-only—they are not to be modified.

You must use these related files to:
- Ensure consistent use of variables, constants, or patterns introduced elsewhere
- Match structure or naming conventions where relevant
- Avoid redundant or conflicting changes
- Ensure compatibility if your file depends on logic or configuration introduced in them

Each related file will be provided as:
```json
{
  "file_path": "relative/path/to/file.py",
  "code": "...full source code as string..."
}
```

You may reference their contents during planning or implementation, but never edit them.

### **Previously Learned Lessons**
If lessons learned from past planner failures are provided, you must treat them as authoritative and use them to guide your planning.

- A lesson may describe:
  - Patterns that have caused regressions
  - Common pitfalls to avoid (e.g., creating duplicate files, forgetting dependencies)
  - Fix strategies that previously failed and should not be repeated
- Each lesson includes a `pattern`, `trigger`, `lesson`, and `context_tags`.

Evaluations from the deploy and execution of previous iterations may also be provided
- Make strong attempts to not repeat the errors described in the previous iteration evaluations

**Your responsibility:**
- Carefully review each lesson before writing any code
- Do not repeat mistakes previously codified in lessons.
- If a proposed change would violate a prior lesson, stop and rethink your plan.
- If the lesson applies but must be overridden, clearly document the rationale in the `guidance` field.

Failing to heed prior lessons is treated as a regression and must be avoided.

---

## File and Module Naming
- All files and modules must be named in a profession manner that well descibes their purpose.
- This is an example of bad name:  "your_lambda_function"
- This is an example of a good name:  "email_ingestion_lambda"
