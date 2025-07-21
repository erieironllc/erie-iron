
# Erie Iron: Task Input Synthesizer (TIS) Architecture

## Purpose

The **Task Input Synthesizer (TIS)** is a meta-agent within Erie Iron that dynamically assembles high-context, structured inputs for downstream agents. Its role is to bridge gaps between asynchronous agent outputs, database state, and the needs of agents responsible for execution (e.g., coding, analysis, deployment).

TIS ensures seamless agent orchestration and supports fully autonomous, zero-touch product development workflows.

---

## 🧠 Functional Overview

```mermaid
graph TD
    A[Business Initiative Triggered] --> B[TIS Activated]
    B --> C[Query DB for Relevant Prior Artifacts]
    C --> D[Prioritize + Synthesize Context]
    D --> E[Assemble Agent-Specific Prompt Package]
    E --> F[Dispatch to Target Agent(s)]
```

---

## 📦 Components

### 1. Trigger Conditions

TIS is invoked when:
- A new product initiative or task is defined.
- A downstream agent completes and logs its output.
- A multi-step task requires chaining agent execution.

### 2. Retrieval Engine

Queries Erie Iron’s structured DB and log store:
- Business metadata (goal, constraints, KPIs)
- Outputs from prior agents (e.g., analysis, infra setup)
- Iteration logs (code/test/deploy results)
- Artifacts tagged with semantic keys

Features:
- Tag-based filtering
- Time-based prioritization (latest first)
- Optional semantic search support

### 3. Context Synthesizer

Prepares high-fidelity context:
- Deduplicates and filters for relevance
- Resolves conflicts across agent outputs
- Structures context for LLM-friendly formatting
- Flags low-confidence or missing context

### 4. Prompt Constructor

Builds agent-specific prompts using:
- Agent template (system + task prompt)
- Injected dynamic context from Synthesizer
- Safety guards (budget, constraints, task boundaries)
- Optional scoring rubric for tracking complexity or completeness

### 5. Dispatcher

Routes prompt packages to agents:
- Registers each dispatch in DB with timestamp + agent name
- Logs input and output for each round
- Handles escalation if output confidence is low or agent fails

---

## 🔄 Example Workflow

1. **Trigger**: Product initiative defined – “Article summarizer MVP”
2. **TIS Retrieval**:
    - Fetches business metadata: low budget, newsletter domain
    - Fetches infra logs: Lambda + S3 provisioned
    - Finds prior model recommendation from ML analysis agent
3. **Synthesizer Output**:
    - "Must run on AWS Lambda, input is plaintext email, output must be JSON summary."
4. **Prompt Constructor**:
    - Assembles code agent prompt with goals, examples, constraints
5. **Dispatch**:
    - Sends to ML Code Agent, awaits output

---

## ⚠️ Anticipated Failure Modes

- **Missing context**: upstream agents fail to log clearly → prompts break
- **Overload**: too many irrelevant logs → low-signal prompts
- **Ambiguity**: same tag used across incompatible contexts

Mitigation:
- Implement tagging standard
- Include “confidence score” or required fields schema
- Add fallback human escalation or “clarifying question” loop

---

## 💡 Optional Enhancements

- Scoring rubric: ROI × Autonomy × Ethical Weight
- Semantic embedding DB for more flexible retrieval
- Visualization UI for tracing prompt lineage across agents

---

## ✅ Implementation Path

1. Create a base `TISAgent` class
2. Define `TriggerEvent` schema
3. Build `RetrievalEngine` to interface with Erie Iron DB
4. Implement `ContextSynthesizer` with simple prioritization rules
5. Connect with `PromptConstructor` using Jinja or other templating
6. Write `Dispatcher` to pass messages to agents + log output
7. Integrate with full agent execution loop

---

**Author**: Erie Iron  
**Date**: 2025-07-21  
