# 🧠 Erie Iron – Unified Execution Flow 

This document outlines the full agent-based execution architecture of Erie Iron, LLC

---

## 🎯 Erie Iron’s Top-Level Objective

Erie Iron exists to:
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
     - Launch the new business via CEO Agent
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

### 5. 🧠 CEO Agent (Business CEO)
**Agent**: `CEO Agent`

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

- Converts high-level tasks into orchestrated capability calls
- Calls the Capability Identifier Agent to identify:
  - Existing capabilities to reuse
  - New capabilities to define
- Returns full spec for downstream execution

---

### 6a. 🧩 Capability Identification  
**Agent**: `Capability Identifier Agent`

- Supports the Task Decomposer Agent
- Identifies required capabilities for a task
- Distinguishes:
  - Which capabilities already exist
  - Which must be newly defined
- Outputs new capability specs in a structured format
- Ensures capabilities are:
  - Reusable across businesses
  - Granular and testable
  - Legal and ethical to execute autonomously

→ New capabilities are passed to the Capability Manager or Builder for implementation

---

### 7. 📈 Sales & Marketing Planning
**Agent**: `Sales & Marketing Agent`

- Defines personas and growth strategies
- Works within cost limits (no paid ads unless justified)
- Outputs plans and strategy-linked capabilities

→ Feeds into CEO Agent task planning

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
- Appears in CEO Agent daily report

---

## 📬 Daily Summary Loop

- Each CEO Agent sends a daily business report:
  - KPIs
  - Completed tasks
  - Blockers
  - JJ alerts
  - Learning material if needed

---

## 🔁 System Flow Summary

| Role             | Description                                         |
|------------------|-----------------------------------------------------|
| Portfolio Leader | Approves/terminates businesses, allocates resources |
| Business Finder  | Proposes new opportunities                          |
| Plan Structurer  | Converts business idea to structured format         |
| Business Analyst | Scores idea for launch                              |
| CEO Agent        | CEO of the business, defines goals & tasks          |
| Task Decomposer  | Breaks down tasks into technical steps              |
| Sales Agent      | Designs customer acquisition & persona strategy     |
| Execution Engine | Runs tasks via pub/sub                              |
| JJ               | Human override for blocked or escalated tasks       |
