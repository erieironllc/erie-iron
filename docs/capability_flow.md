# Erie Iron Capability Flow (v1)

## Entry Points

There are two main entry points into the Erie Iron flow:

### 1. User-Initiated Idea
- You provide a rough idea or business concept
- ➝ Sent to `Business Structuring Agent`

### 2. Autonomous Idea Generation
- The `Business Finder Agent` proposes a new idea
- ➝ Sent to `Business Structuring Agent`

---

## Core Flow

```
               ┌─────────────────────────────┐
               │   [Raw Idea or Prompt]      │
               └────────────┬────────────────┘
                            │
                            ▼
               ┌─────────────────────────────┐
               │ Business Structuring Agent  │
               │  → Organizes idea into      │
               │    a structured plan        │
               └────────────┬────────────────┘
                            │
                            ▼
               ┌─────────────────────────────┐
               │ Business Analyst Agent      │
               │  → Evaluates feasibility,   │
               │    risks, and potential     │
               └────────────┬────────────────┘
                            │
                            ▼
               ┌─────────────────────────────┐
               │ Capability Identifier Agent │
               │  → Lists capabilities needed│
               └────┬────────────┬───────────┘
                    │            │
        ┌───────────▼───┐    ┌───▼────────────┐
        │ Autonomous    │    │ Autonomous     │
        │ Coding Agent  │    │ Coding Agent   │
        │ (Capability A)│    │ (Capability B) │
        └────┬──────────┘    └────┬───────────┘
             │                       │
             ▼                       ▼
     ┌──────────────┐       ┌────────────────┐
     │ Output Review │       │ Output Review │
     └──────┬────────┘       └──────┬────────┘
            ▼                       ▼
         [Done]                 [Done]
```

---

## Agent Responsibilities

### 🔹 Business Structuring Agent
- Input: Raw idea
- Output: Structured plan (value prop, user, model, etc.)
- Next: `Business Analyst Agent`

### 🔹 Business Analyst Agent
- Input: Structured plan
- Output: Full analysis (market, risks, ROI, go/no-go score)
- Next: `Capability Identifier Agent`

### 🔹 Capability Identifier Agent
- Input: Business concept
- Output: List of capabilities (in plain English)
- Next: `Autonomous Coding Agent` (one per capability)

### 🔹 Autonomous Coding Agent
- Input: Capability spec
- Output: Working, tested code + CLI + docs
- Next: `Output Reviewer Agent`

### 🔹 Output Reviewer Agent
- Input: Code + test artifacts
- Output: Evaluation summary, confidence score, flag for issues
- Can send back for revision

---

## Optional Future Agents

- **Execution Tracker Agent:** Monitors the state of capabilities and logs progress
- **Self-Reflection Agent:** Learns from past business performance and suggests strategy shifts
- **Prompt Engineer Agent:** Fine-tunes system prompts based on results

---

## Design Principles

| Principle              | Description                                                  |
|------------------------|--------------------------------------------------------------|
| Single Responsibility  | Each agent does one job only                                 |
| Composable Outputs     | Outputs are structured JSON, easy to chain                   |
| Autonomy Rating        | Used to skip unnecessary manual reviews                      |
| Interruptible Flow     | Human can jump in at any step to modify or override          |
