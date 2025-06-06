# 🧠 Erie Iron – Unified Execution Flow (Updated)

This document outlines the full agent-based execution architecture of Erie Iron, incorporating recent upgrades to the Portfolio Leader Agent and agent handoffs.

---

## 🎯 Erie Iron’s Top-Level Objective

Erie Iron exists to explore and advance the frontier of autonomous AI agents. Its core mandate is to:
- **Make money** legally and ethically for its owner (JJ)
- Run **lean**, **autonomous**, and **scalable** businesses
- Operate as a portfolio of individually-managed ventures

---

## 🏛️ Portfolio-Level Governance

### 1. 🧠 Portfolio Leadership
**Agent**: `Portfolio Leader Agent`

- Acts as Erie Iron’s strategic operator — like Warren Buffett at Berkshire Hathaway
- Operates on a daily/periodic loop to:
  1. Review all **existing businesses**:
     - Request updated business analysis and legal risk reports
     - Shut down underperforming or risky businesses
  2. Assess current **execution capacity** (budget + infrastructure)
  3. If capacity allows:
     - Loop with Business Finder and Analyst until a strong opportunity is found
     - Launch the new business via Brain Agent
  4. If capacity is **insufficient** but the opportunity is strong:
     - Escalate to JJ with rationale, estimated value, and required resources

---

## 💡 Business Creation Pipeline (Triggered by Portfolio Leader)

### 2. 💡 Business Discovery
**Agent**: `Business Finder Agent`

- Explores novel, low-cost, scalable ideas
- Avoids illegal, unethical, or restricted domains
- Outputs structured business opportunity

→ Passed to Business Plan Structurer

---

### 3. 🧱 Business Structuring
**Agent**: `Business Plan Structuring Agent`

- Converts freeform ideas into structured plans
- Defines: functions, audience, monetization, growth paths
- Creates machine-readable plan

→ Passed to Business Analyst

---

### 4. 📊 Business Evaluation
**Agent**: `Business Analyst Agent`

- Assesses feasibility, TAM, time-to-profit, competition, risks
- Outputs:
  - Score (1–10)
  - Estimated startup costs, macro-alignment
  - Recommended go/no-go

→ Passed to Portfolio Leader for launch decision

---

## 🚀 Active Business Operation (Per Business)

### 5. 🧠 Brain Agent (Business CEO)
**Agent**: `Brain Agent`

- Runs a single approved business autonomously
- Outputs tasks aligned to KPIs and goals
- Ensures all actions are:
  - Legal, ethical
  - Budget-conscious
  - Non-annoying to users
- Does not define implementation

→ Tasks passed to Task Decomposer Agent

---

### 6. ⚙️ Task Decomposition
**Agent**: `Task Decomposer Agent`

- Converts high-level tasks into required capability calls
- Identifies:
  - Existing capabilities to reuse
  - New capabilities to define
- Returns full spec for downstream execution

---

### 7. 📈 Sales & Marketing Planning
**Agent**: `Sales & Marketing Agent`

- Defines personas and growth strategies
- Works within cost limits (no paid ads unless justified)
- Outputs plans and strategy-linked capabilities

→ Feeds into Brain Agent task planning

---

### 8. 🔁 Execution Engine
**Component**: Pub/Sub Task Runner

- Listens for AUTONOMOUS tasks
- Executes capabilities
- Logs outputs, status, and failures
- Triggers retries or escalations

---

### 9. 👤 JJ Escalation
**Component**: Human-in-the-loop

- Receives all HUMAN-mode tasks
- Can unblock execution, provide credentials, approve risky actions
- Appears in Brain Agent daily report

---

## 📬 Daily Summary Loop

- Each Brain Agent sends a daily business report:
  - KPIs
  - Completed tasks
  - Blockers
  - JJ alerts
  - Learning material if needed

---

## 🔁 System Flow Summary

| Role | Description |
|------|-------------|
| Portfolio Leader | Approves/terminates businesses, allocates resources |
| Business Finder | Proposes new opportunities |
| Plan Structurer | Converts idea to structured format |
| Business Analyst | Scores idea for launch |
| Brain Agent | CEO of the business, defines goals & tasks |
| Task Decomposer | Breaks down tasks into technical steps |
| Sales Agent | Designs customer acquisition & persona strategy |
| Execution Engine | Runs tasks via pub/sub |
| JJ | Human override for blocked or escalated tasks |

# 🧠 Erie Iron – Unified Execution Flow (Modular Agent Design)

This document outlines the updated agent-based execution architecture of Erie Iron, reflecting the modular control flow and orchestration logic introduced in July 2025.

---

## 🎯 Erie Iron’s Top-Level Objective

Erie Iron exists to explore and advance the frontier of autonomous AI agents. Its core mandate is to:
- **Make money** legally and ethically for its owner (JJ)
- Run **lean**, **autonomous**, and **scalable** businesses
- Operate as a portfolio of independently-managed businesses

---

## 🏛️ Portfolio Governance

### 1. 🧠 Portfolio Leader Agent

- Acts as the Warren Buffett of Erie Iron
- Executes periodically (e.g., daily) to review, adjust, and grow the business portfolio
- Its logic is orchestrated via Python code calling smaller agents in sequence

#### Responsibilities:
1. **Review All Existing Businesses**
   - Calls the Business Analyst Agent and Legal Review Agent for fresh data
   - Passes the data to the Business Decision Agent
   - Decision options:
     - `MAINTAIN`
     - `INCREASE_BUDGET`
     - `DECREASE_BUDGET`
     - `SHUTDOWN`
   - If change is needed, generates a task (same task type used across system)

2. **Assess System Capacity**
   - Invokes Capacity Assessment Agent
   - Determines if there's capacity to start a new business

3. **Launch New Businesses (if capacity allows)**
   - Loops through:
     - Business Finder Agent → Business Idea
     - Business Structurer Agent → Structured Plan
     - Business Analyst Agent → Recommendation
   - If a suitable business is found, launches a Brain Agent
   - If no capacity but high-value idea is found, escalates to JJ

---

## 🛠️ Business Creation Pipeline

### 2. 💡 Business Finder Agent
- Generates novel, low-cost, scalable ideas
- Avoids illegal, unethical, or restricted domains

→ Sent to Business Structuring Agent

### 3. 🧱 Business Structuring Agent
- Turns freeform ideas into structured business plans
- Defines: value proposition, core functions, audience, monetization, etc.

→ Sent to Business Analyst Agent

### 4. 📊 Business Analyst Agent
- Scores viability, TAM, monetization, cost-to-launch, time-to-profit, and risk
- Returns a numeric score and recommendation

---

## 🚀 Business Operation (One Brain Agent per Business)

### 5. 🧠 Brain Agent
- Acts as the **CEO** of one approved business
- Defines tasks aligned with goals and KPIs
- Ensures actions are:
  - Legal & ethical
  - Budget-aligned
  - Non-annoying to users

→ Outputs tasks (AUTONOMOUS or HUMAN)

---

### 6. ⚙️ Task Decomposer Agent
- Takes tasks from Brain Agent
- Returns required:
  - Capabilities to use (existing)
  - Capabilities to build (new)

→ Passed to Execution Engine or Capability Builder

---

### 7. 📈 Sales & Marketing Agent
- Defines growth strategies and personas
- Outputs capabilities and tactics to be executed
- Feeds into Brain Agent task planning

---

## 🔁 Execution & Escalation

### 8. 🔁 Execution Engine
- Listens for AUTONOMOUS tasks via pub/sub
- Runs capabilities and logs results
- Retries or escalates failures

### 9. 👤 JJ Escalation
- HUMAN tasks routed to JJ
- Includes credential requests, risky actions, or approvals

---

## 📬 Daily Reporting

- Each Brain Agent sends JJ a daily digest:
  - Task status
  - KPI metrics
  - Blockers
  - Learning content

---

## 🔄 System Summary

| Role | Description |
|------|-------------|
| Portfolio Leader | Oversees all businesses; approves launches/shutdowns |
| Business Finder | Proposes new ideas |
| Structurer | Converts ideas into structured plans |
| Analyst | Scores viability and ROI |
| Brain Agent | Acts as CEO for one business |
| Task Decomposer | Breaks tasks into capability-level calls |
| Sales Agent | Defines growth personas and tactics |
| Execution Engine | Runs capabilities via pub/sub |
| JJ | Handles human tasks and escalations |