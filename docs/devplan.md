# Development Plan: Erie Iron "Coding" Task Type

This plan introduces a new task type focused on broader, multi-file application development efforts. Unlike current function-style tasks, this type facilitates full-stack engineering workflows by combining prompt-driven development, branching logic, disposable test environments, and context-aware planning.

---

## ☑️ Task 1: Define the "Coding" Task Type

**Context**  
We need a formal representation of this new task type in the task schema.

**Implementation Ideas**  
- Introduce a new enum value: `TaskType.CODING`
- Extend the task planner and executor logic to handle Coding-specific `execute()` behavior.
- Add metadata fields like:
  - `repo`: GitHub repo to work on
  - `goal_description`: high-level app feature or fix
  - `acceptance_test`: optional entrypoint or file for verification

---

## ☑️ Task 2: Implement Standard `execute()` Routine

**Context**  
All Coding tasks should follow a structured high-level flow:
1. Branch the Business's GitHub repo
2. Generate an acceptance test with the self-driving coder
3. Implement the feature
4. Iterate until the test passes locally
5. Deploy to a disposable or staging environment
6. Validate test in environment
7. If failed, loop back to step 2
8. If passed, merge to main

**Implementation Ideas**  
- Use GitHub API or CLI for branch, push, and merge
- Store step status to enable retry/resume
- Add logging per step to track automated decisions

---

## ☑️ Task 3: Integrate Self-Driving Coder for Multi-File Support

**Context**  
The self-driving coder must be enhanced to operate across multiple files.

**Implementation Ideas**  
- Accept multiple files as input to `codewriter--planning.md`
- Implement logic to:
  - Identify the relevant files using a context search (see Task 4)
  - Return diffs or patch sets
- Maintain memory of files already involved in the task

---

## ☑️ Task 4: Build Search Index Capability for Codebase

**Context**  
We cannot fit entire repos into LLM context. Need intelligent narrowing of files.

**Implementation Ideas**  
- Index repo by file, class, and function using embeddings
- Build `search_codebase(query: str) -> List[Path]` capability
- Allow filtering by recency, path pattern, token size, language
- Store embeddings in a local vector store (e.g., FAISS or LanceDB)

---

## ☑️ Task 5: Create `CodeNavigator` Capability

**Context**  
This tool serves as the LLM-friendly layer over file I/O and repo analysis.

**Implementation Ideas**  
- Functions:
  - `get_code_context(query)`
  - `write_code_changes(patch)`
  - `validate_patch_syntax()`
- Internal logic:
  - Use Git to track file state
  - Use tree-sitter or similar for structured edits

---

## ☑️ Task 6: Disposable Test Environments

**Context**  
Each task should run isolated tests to ensure correctness before merging.

**Implementation Ideas**  
- Use AWS ECS Fargate or Ephemeral Lambda containers
- Optionally use `task-{UUID}` as env ID
- Provision infra with CDK, Pulumi, or CloudFormation
- Define a universal `run_acceptance_test.sh` in each repo

---

## ☑️ Task 7: Reporting & Feedback Integration

**Context**  
Each Coding task should emit structured results for JJ’s 7am email and for debugging.

**Implementation Ideas**  
- Record:
  - Which files were changed
  - Whether test passed locally and in-env
  - Final Git commit hash
- Generate:
  - JSON summary for system use
  - Human-readable snippet for email

---

## ☑️ Task 8: Optional – Escalation Logic for Merge Conflicts

**Context**  
Sometimes a PR cannot be merged automatically, but we shouldn't lose the work.

**Implementation Ideas**  
- If merge fails:
  - Tag task as `BLOCKED_MERGE_CONFLICT`
  - Log the full diff and conflict error
  - Send a human-readable note to JJ
- Allow rebase-and-retry option as fallback

---

## 🚀 Future Opportunities

- Use Coding tasks for documentation updates, performance improvements, and full feature builds.
- Automatically link accepted tasks to tracked business KPIs or Goals.
- Add metrics tracking: time to merge, pass rate, number of iterations.
