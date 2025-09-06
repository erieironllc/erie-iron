## Using agent tools chat for LLM interactions

If the planned code needs to query an LLM, the code must use the single helper `llm_chat` and must not introduce any new SDK clients or custom HTTP calls. This keeps usage consistent, auditable, and low surface area.

### When to use
- Generating short structured text such as JSON payloads, summaries, or small code snippets that are post-validated.
- Converting logs to a structured error report for diagnostics.
- Never for large file generation or unbounded loops. If the task would require long generation or streaming, stop and emit `blocked` with category `surface_area`.

### How to call
- Import path: `from erieiron_public.agent_chat import llm_chat`
- Required signature used by application code:
  - `llm_chat(tag: str, llm_intelligence: LlmIntelligence, system_prompt: str, user_prompts: list[str], response_format: str | dict | pathlib.Path | None) -> str`
- `tag` must be set to this value:  `<business_tag>`
- Choose `llm_intelligence` by cost and complexity:
  - LOW for simple deterministic transforms.
  - MEDIUM for multi step reasoning that is still bounded.
  - HIGH only when evaluator feedback shows lower levels failed or the task is inherently complex.

### Response format and validation
- Prefer a JSON schema to enforce structure when parsing the model output.
- Allowed `response_format` inputs:
  - `dict` with a JSON Schema object.
  - `str` that is a JSON string of the schema.
  - `pathlib.Path` to a local schema file committed in repo.
- Application code must validate the model output against the schema before use and raise a clear error on validation failure. Do not proceed on partial parses.

### Timeouts, retries, and quotas
- Set a caller side timeout of 15 seconds per request unless the evaluator requires otherwise.
- Allow a single retry on transient errors only. Do not retry on validation failures.
- Never place LLM calls inside tight loops. Batch prompts when possible.

### Logging and PII
- Log only non sensitive metadata: tag, intelligence level, and high level purpose. Do not log prompts that may contain secrets, and never log model responses that include user data.
- Propagate the `tag` value (`<business_tag>`) in any surrounding request or job logs for attribution.

### Testing guidance
- Unit tests must stub the `llm_chat` call at the module boundary and assert that:
  - It is invoked at most once per operation.
  - The tag and intelligence level are set as planned.
  - Output is validated against the schema before use and failures raise deterministic errors.
- Acceptance tests must not invoke real LLMs. They should simulate responses via fakes that return valid and invalid payloads to exercise validation paths.

### Planner output requirements when LLM is used
- In `code_files[*].guidance`, call out where `llm_chat` is invoked and why the selected intelligence level is appropriate.
- In `code_files[*].instructions`, include:
  - The exact import statement for `llm_chat`.
  - The tag value `<business_tag>` is used
  - The system prompt and user prompts or where they are constructed.
  - The full JSON schema if structure is required and where it lives if using a file.
  - The validation step and the exact exception to raise on failure.
- If using a schema file, include that file in the same iteration and place it under `core/schemas/` with a clear name.

### Minimal example for the code writer

```python
from erieiron_public.agent_chat import llm_chat
from erieiron_public.agent_chat import LlmIntelligence
from pathlib import Path
import json

def summarize_errors(log_text: str) -> dict:
    schema_path = Path("core/schemas/error_summary.schema.json")
    # One call, bounded output, validated
    raw = llm_chat(
        tag="<business_tag>",
        llm_intelligence=LlmIntelligence.MEDIUM,
        system_prompt="You convert logs into a compact JSON error summary.",
        user_prompts=[log_text],
        response_format=schema_path,
    )
    data = json.loads(raw)
    # Application must validate against the schema before use
    validate_json_against_schema(data, schema_path)  # writer implements this utility
    return data
```

