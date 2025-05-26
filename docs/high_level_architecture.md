# Erie Iron high-level system components

## ErieIron System Agent
This is the main controller orchestrator for Erie Iron.  The system agent similar to the board of directors (or CEO) of a large multi-industry conglomerate.

### Execution Plan
1.  Generate profit legally and ethically.  This is an efficient machine for earning money.
2.  Run as autonomously as possible.
2.  Keep JJ informed on 
    * status of the overall objective (profit, money)
    * for each business, report on the performance, risks, short term outlook, long term outlook
    * identify articles and learning content for JJ to read to know more about the Erie Iron businesses
    * the system agent will send JJ a status update email at 7am every morning

### Execution Plan
1.  Run in a docker container deployed to AWS EC2
2.  Process will run a daemon or be triggered by a regularly firing lambda
3.  Process will identify business opportunities to legally and ethically generate profit as autonomously as possible.  Each opportunity will be stored as a "Business"
4.  When the system agent identifies a new "Business" opportunity, it calls out to the "ErieIron Business Manager" to create the business.  
5.  When the system agent identifies an existing business is not performing (ie it is not turning a profit or will not turn a profit in an acceptable amout of time) or is too high risk or is operating illegally or non-ethically, the system agent instructs the business manager to shut down the business
5.  Daily, the system agent will review each business and send JJ a status update email


## ErieIron Capability Manager
Manages the Erie Iron capabilities.  A capability can either be executed autonomously or require human execution

### Execution Plan
1.  When the Business Manager requests a "Capability"
    a.  The capability manager identifies if it already has this capability built. If the capability exists it should return the API endpoint to the existing capability.
    b.  If the capability does not exist, the capability manager will identify if it can build it autonomously or if it requires a human to build
    c.  If the capability manager identifies it can build a capability autonomously, it should build it itself autonomously. If in the course of building the capability it identifies it needs new capabilities it goes back to step "b" to identify if it can build it itself or if it needs a human to build it
    d.  If the capability manager identifies that it needs a human to help build a capability, it shall email JJ requesting the addition of a capability. Examples of this would be where credentials need to be set up for a third-party integration or if money is needed, etc.  Eventually it would be cool if if the capability manager was able to manage third-party integrations and the money itself, but it's possible that in the early days it will need a human a.k.a. JJ to do this
    e.  The capability manager will continuously assess whether capability is legal and ethical, and assess the risk associated with this capability. It will keep a running log of its assessments

For capabilities that cannot be built autonomously, the system follows a structured escalation path:
1. Request Capability & Escalate
   - Send email to JJ detailing:
     - Required capability
     - Justification and urgency
     - Suggested implementation steps

2. Retry on No Response
   - Retry after 24 hours (configurable)
   - Maximum of 3 retries

3. Track Escalation State
   - Log all escalation attempts and timestamps
   - Maintain state for each escalated capability

4. Escalation Reporting
   - Include escalation summary in JJ’s 7am daily report
   - Mark business as blocked until escalation is resolved

### Highlevel datastructure
#### Capability
- name -> str
- description -> str
- endpoint -> url
- estimated_cost_per_execution -> float
- version -> str
- used_by_businesses -> list[str]

#### CapabilityExecution
- capability -> Capability
- business -> Business
- executor -> enum (or foreign key to person?):   AUTOMOMOUS, HUMAN (maybe support multiple human executors)
- state -> enum:   NEW,  PENDING,  RETRYING,  BLOCKED, EXPIRED, RESOLVED
- result -> enum: SUCCESS, FAIL_RETRYABLE, FAIL_FATAL, TIMEOUT, BLOCKED_DEPENDENCY
- retry_count ->
- start_time
- end_time
- cost


## ErieIron Business Manager
Manages the Erie Iron business portfolio

### Execution Plan
1.  When the System Agent requests the creation of a new business, the business manager
    a.  Develops a business plan, including short|medium|long term kpis, goals, milestones and creates the Business entity
    b.  Develops the plan for how to legally and ethically shut down the business in the case the business needs to be shutdown
2.  When the System Agent requests the shutdown of a business
    a.  Executes the shutdown plan
3.  The business manager should continuously manage the business:
    a. in the course of building or managing the business, the business manager should identify "Capabilities" the it needs to achieve the business goals.  For each required "Capability", call out to the "Capability Manager" to fetch the "Capability".  The Capability Manager will return a list of api endpoints the business can call to execute the capability
    b. respond to email correspondence, user help requests, etc
    c. assess risks (legal, ethical, competitive, etc) and keep a running log of risks and mitigations
4. To support autonomous optimization and prevent premature shutdowns, ErieIron incorporates a continuous feedback loop in each business
    a. Start Business  
       Initialize with defined KPIs, milestones, and required capabilities.

    b. Track Capability Outcomes  
       Log performance of each capability (success/failure, latency, cost, etc.)

    c. Evaluate Performance  
       Compare actual outcomes against planned KPIs at regular intervals.

    d. Adjust Plan  
       If underperformance is detected:
       - Identify root cause (e.g. faulty capability, incorrect assumption)
       - Adapt the business model, pricing, or user engagement strategy

    e. Shutdown  
       If multiple adaptation attempts fail, trigger the ethical/legal shutdown procedure.

### Highlevel datastructure
#### Business
- name -> str
- goals -> list[Goal]
- capabilities -> list[Capability]
- shutdown_plan -> ShutdownPlan

#### Goal
- name -> str
- type -> enum: KPI, MILESTONE
- target_value -> float | str
- actual_value -> float | str
- status -> enum: ON_TRACK, AT_RISK, OFF_TRACK
- evaluation_interval_days -> int

#### ShutdownPlan
- triggers -> list[Trigger]
- legal_review_completed -> bool
- ethical_review_completed -> bool
- data_purge_strategy -> str
- user_communication_plan -> str

#### Trigger
- condition -> str  # e.g., "kpi.growth_rate < 0.01 for 30 days"
- severity -> enum: LOW, MEDIUM, HIGH
