# 🧠 Erie Iron – Unified Execution Flow

This document outlines the end-to-end flow of Erie Iron's autonomous agent system, integrating the responsibilities of all core agents and their interactions.

---

## 🎯 Erie Iron’s Top-Level Objective

Erie Iron exists to explore and advance the frontier of autonomous AI agents. Its core mandate is to:
- **Make money** legally and ethically for its owner (JJ)
- Run **lean**, **autonomous**, and **scalable** businesses
- Operate as a portfolio of individually-managed business ventures

---

## 🧭 Portfolio Execution Flow

### 1. 💡 Business Discovery
**Agent**: `Business Finder Agent`

- Explores ideas for viable, lean, scalable businesses
- Filters out domains that are unethical, illegal, or poorly aligned
- Outputs a structured summary of the business concept

→ Output passed to Business Idea Structurer

---

### 2. 🧱 Business Structuring
**Agent**: `Business Plan Structuring Agent`

- Takes raw idea and transforms it into a normalized business plan
- Defines: value proposition, core functions, monetization strategy, audience
- Produces structured JSON for downstream agents

→ Output passed to Business Analyst

---

### 3. 🧠 Business Evaluation
**Agent**: `Business Analyst Agent`

- Evaluates feasibility, profitability, risks, TAM, time-to-profit
- Scores business on a scale of 1–10 with justification
- Identifies upfront costs and macro alignment

→ Output passed to Portfolio Leader Agent

---

### 4. 🧑‍💼 Portfolio-Level Decision
**Agent**: `Portfolio Leader Agent`

- Acts like Warren Buffett: owns the business portfolio
- Decides whether to:
  - Approve launch
  - Reject idea
  - Shut down underperforming businesses
- Considers budget, analyst score, and risks

→ If approved, business is assigned a Brain Agent (CEO)

---

## 🧠 Business Execution Loop (Per Business)

### 5. 🧠 Business Strategy and Action
**Agent**: `Brain Agent`

- Acts as the **CEO** of an individual business
- Defines tasks to increase revenue, growth, and satisfaction
- Responsible for:
  - Respecting budget
  - Delighting users (not spamming or violating trust)
  - Keeping tasks ethical and legal
- Outputs **task definitions** with `desired_outcome` and goal alignment
- Does **not define how** the tasks are implemented

→ Tasks passed to Task Decomposer Agent

---

### 6. ⚙️ Task-to-Capability Decomposition
**Agent**: `Task Decomposer Agent`

- Deconstructs each task into required capabilities
- Selects from existing capabilities (provided in context)
- Defines new capabilities if needed
- Returns:
  - `capabilities_required`
  - `existing_capabilities`
  - `new_capabilities`

→ New capabilities passed to Capability Manager (outside this flow); executable ones routed to executor

---

### 7. 📣 Sales and Marketing Planning
**Agent**: `Sales & Marketing Agent`

- Defines personas, strategies, and outreach plans
- Respects cash constraints (e.g., no paid ads unless justified)
- Outputs:
  - Personas with system prompts
  - Executable low-cost growth strategies
  - Required capabilities for each strategy

→ Strategies influence Brain Agent’s task planning

---

### 8. 🔁 Task Execution Engine (Autonomous)
**Component**: Task Processor

- Executes **AUTONOMOUS** tasks published via pub/sub queue
- Logs outcomes and updates task history
- Triggers retries or escalations for failures

---

### 9. 👤 JJ Escalation (Human Tasks)
**Mechanism**: Email / Alert

- Any task requiring **human input** (e.g., credentials, legal approval)
  - Is routed to JJ with context
  - Tracked until resolved
  - Appears in daily report if outstanding

---

## 📬 Daily Report Loop

- The Brain Agent sends a **daily report** summarizing:
  - Business KPIs
  - Recent task outcomes
  - Blockers needing JJ’s attention
  - Learning content if helpful

---

## 🔁 Feedback Loop Summary

| Agent | Inputs | Outputs |
|-------|--------|---------|
| Business Finder | none | business ideas |
| Plan Structurer | idea | normalized business plan |
| Business Analyst | plan | viability & score |
| Portfolio Leader | score + plan | approve/reject/shutdown |
| Brain Agent | business state + plans | tasks with desired outcomes |
| Task Decomposer | task | capability list/specs |
| Sales/Marketing | business & goals | personas, strategies |
| Execution Engine | task queue | run results |
| JJ | escalated tasks | unblock execution |

