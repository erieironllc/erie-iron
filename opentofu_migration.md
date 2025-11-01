# OpenTofu Migration Plan

## Executive Overview
- The self-driving coder agent (`erieiron_autonomous_agent/coding_agents/self_driving_coder_agent.py`) tightly couples orchestration, parameter management, and logging to AWS CloudFormation APIs via helpers in `erieiron_common.cloudformation_utils` and direct boto3 calls.
- OpenTofu (the community fork of Terraform) provides cloud-agnostic IaC with rich module ecosystems, drift detection, policy integration, and a sustainable governance model that addresses vendor lock-in concerns while remaining fully compatible with Terraform providers.
- Migration prioritizes faster iteration on AWS infrastructure: we will still deploy workloads to AWS services (accessed via boto3 and other SDKs), but the declarative infrastructure will be managed through OpenTofu instead of CloudFormation.
- The work also positions ErieIron for multi-cloud readiness, reproducible infrastructure environments, and a broader talent pool; it unlocks policy-as-code tooling (e.g., Open Policy Agent, Sentinel equivalents) and integration with existing Terraform ecosystems once we expand beyond AWS.
- Target outcome: retain current automation capabilities while substituting CloudFormation-specific flows with OpenTofu workspaces, state management, and deployment primitives—accelerating iteration speed without regressing agent productivity.
- We will fully retire the CloudFormation code paths as part of this effort—no dual-run period is required because we do not yet serve external customers.

## Current State Snapshot
- `self_driving_coder_agent.py` handles template validation, stack parameter resolution, deployment retries, and log extraction (e.g., `deploy_cloudformation_stack` at lines ~4809-4895 and `validate_cloudformation_template` at ~2667-2759).
- Deployment flows assume two stacks per initiative (`InfrastructureStackType.FOUNDATION` and `InfrastructureStackType.APPLICATION`) and mirror their identities into Docker environments (`sync_stack_identity`, lines ~4720-4789).
- Supporting utilities (`cloudformation_utils`, `cloudformation_log_reader`, `get_stack_parameters`, etc.) mediate retries, status checks, and parameter metadata extracted from CloudFormation templates.
- LLM prompts and evaluation logic explicitly reference “CloudFormation” semantics (e.g., prompt assets `codewriter--aws_cloudformation_coder_tofu.md`, evaluation guidance near lines ~2323-2345, and telemetry structures that store `cloudformation_logs`).

## Migration Objectives
- Replace CloudFormation-specific deployment, validation, and logging with OpenTofu equivalents while keeping agent workflows (plan → build → deploy → evaluate) intact.
- Preserve the application runtime’s ability to interact directly with AWS services (boto3, SDKs, managed databases) so feature work keeps its existing service surface area.
- Maintain parity for secrets and parameter sourcing, stack identity propagation, and rollback diagnostics.
- Execute a direct cutover: once OpenTofu is ready, remove CloudFormation execution paths and associated utilities to simplify the stack.
- Improve infrastructure modularity so future service additions are composed as reusable OpenTofu modules.

## Cloud Platform Strategy
- Stay focused on AWS in the near term to preserve provider-specific optimizations (state backends, IAM integrations, managed databases) while the refactor lands and to maximize iteration speed for new workloads.
- Ensure generated services continue to call AWS primitives directly (boto3, AWS SDKs, managed event buses) without additional abstraction layers so feature teams are not slowed down.
- Design modules, variable contracts, and abstraction layers so that additional providers (Azure, GCP) can plug in with minimal churn when we intentionally expand beyond AWS.
- Favor OpenTofu-native constructs (providers, modules) that already have cross-cloud support to shorten the path to cloud-agnostic deployments when the roadmap requires it.

## Architectural Direction
### IaC Layout
- Introduce `infra/opentofu/` with separate modules for foundation and application concerns; mirror current template decomposition.
- Use opinionated module boundaries (networking, data stores, app runtime) to simplify agent reasoning and human review.
- Capture environment-specific variables in `.tfvars` files generated (or templated) by the agent in place of CFN parameter JSON.

### State & Backend
- Store OpenTofu state in S3 with DynamoDB locking; define backend blocks per environment (`aws` provider) and enforce encryption at rest.
- Maintain namespace tokens (`stack_namespace_token`) as workspace/state identifiers to stay aligned with existing `InfrastructureStack` records.
- Provide tooling to migrate or import existing resources (`tofu import`) before the first agent-led apply.

### Execution Wrapper
- Create `erieiron_common/opentofu_utils.py` to encapsulate:
  - `render_plan()` → wraps `tofu plan`, parsing JSON plan output.
  - `apply()` → handles retries and timeout semantics similar to `deploy_cloudformation_stack`.
  - `show_state()` / `list_outputs()` → surfaces outputs for downstream tasks currently using `get_stack_outputs`.
  - Structured logging that aligns with current `cloudformation_logs` schema, enabling gradual rename to `iac_logs`.
- Wrapper should expose a pythonic API so `self_driving_coder_agent` can swap from `deploy_cloudformation_stack` to `apply_open_tofu_stack` with minimal diff.

### Parameter & Secrets Management
- Translate existing CFN parameter handling to OpenTofu variable files:
  - Map required parameters discovered via `extract_cloudformation_params` to Terraform `variable` definitions with validation rules.
  - Generate `.auto.tfvars.json` files during agent execution, leveraging the same `known_params` logic (see lines ~4891-4966) but serialised as HCL-compatible JSON.
  - Enhance `credential_manager` integration to emit environment variables or Secrets Manager data in formats consumed by the OpenTofu wrapper.

### Logging & Observability
- Capture stdout/stderr from `tofu` commands, persist structured plan/apply JSON, and summarize changes for LLM prompts.
- Replace CloudWatch Log-based polling with direct CLI output stored under `SelfDrivingTaskIteration.cloudformation_logs` (rename to `iac_logs` in follow-up migration).
- Emit resource change timelines to preserve `slowest_cloudformation_resources` insights; map resource addresses to durations using `plan` and `apply` timing data.

## OpenTofu Operational Clarifications
### Secrets Management
- OpenTofu consumes secret material via providers and data sources (AWS Secrets Manager, SSM Parameter Store, HashiCorp Vault, Doppler, etc.) and only persists values in state when the configuration explicitly outputs them; mark variables as `sensitive = true` and avoid printing secrets in logs so they remain masked.
- Continue using remote state backends with server-side encryption (S3 + SSE-KMS today) and restrict IAM access so only the automation role and incident responders can read or modify state artifacts.
- During agent runs, generate `.auto.tfvars.json` files with references (ARNs, parameter paths) instead of raw credentials and let `credential_manager` inject the actual secrets through environment variables at apply time.

### Role Permissions
- Authenticate the AWS provider by assuming a purpose-built deployment role with least-privilege policies that cover OpenTofu state access, target resource APIs, and secrets retrieval, mirroring current `credential_manager` flows.
- Structure provider blocks so they can be swapped for other clouds (Azure service principals, GCP service accounts) via aliases, enabling multi-cloud support without rewriting module internals.
- Encapsulate IAM policy definitions inside dedicated modules to enforce consistent permissions and simplify audits when new services are added.

### Networking
- Model network primitives (VPCs, subnets, load balancers, security groups) in reusable modules parameterized by CIDR, tags, and ingress/egress rules; default to opinionated safe configurations that the agent can extend.
- Lean on well-maintained community modules for AWS today while shaping variable names to match analogous Azure VNet or GCP VPC constructs, keeping cross-cloud parity in reach.
- Treat network policies as code by codifying security baselines (no 0.0.0.0/0 without justification, required TLS listeners, etc.) that the agent and human reviewers can validate quickly.

### Databases & PostgreSQL Portability
- Provision managed PostgreSQL instances through provider resources (`aws_db_instance`, `azurerm_postgresql_flexible_server`, `google_sql_database_instance`) wrapped in a shared module that standardizes engine versioning, storage, and maintenance windows.
- Surface connection details via sensitive outputs consumed by application services while ensuring passwords rotate through Secrets Manager or Vault, not stored in OpenTofu state.
- For truly cloud-agnostic PostgreSQL, optionally support containerized Postgres (e.g., on Kubernetes, Nomad) controlled by separate modules, enabling workload portability without abandoning managed services where they fit best.

### Prompt & UX Adjustments
- Update LLM prompt assets (`prompts/codewriter--aws_cloudformation_coder_tofu.md`, etc.) to reflect OpenTofu vocab, modules, and best practices.
- Adjust evaluation instructions (lines ~2323-2345) so success criteria revolve around successful `tofu apply` and zero diffs.
- Provide guardrails around `locals`, `for_each`, and module usage to prevent agent confusion.

## Implementation Plan (Phased)
### Phase 0 – Discovery & Tooling Alignment
- Inventory CloudFormation entry points within `self_driving_coder_agent.py`, `cloudformation_utils`, and dependent services to scope the direct replacements.
- Validate OpenTofu CLI availability in CI/CD and local environments; decide packaging (pre-built binary vs. `brew`/`asdf`) and capture current deployment lead time as a baseline metric.
- Prototype an AWS-only OpenTofu configuration for the foundation/application stacks to confirm resource coverage and highlight iteration bottlenecks.

### Phase 1 – AWS OpenTofu Foundations
- Build `opentofu_utils.py` with AWS-aware helpers for `tofu init/plan/apply`, output parsing, and error normalization; ensure it enforces fast-fail behavior suited for rapid iteration.
- Define state backends (S3 + DynamoDB) and workspace conventions aligned with existing `InfrastructureStack` identifiers so agent code keeps its namespace assumptions.
- Introduce a short-lived configuration toggle to gate rollout, while planning to delete the CloudFormation path once OpenTofu passes acceptance.

### Phase 2 – Module Migration for AWS Services
- Translate the current CloudFormation templates into OpenTofu modules that target the same AWS services (networking, compute, databases, messaging) and preserve naming conventions for boto3 consumers.
- Leverage iteration speed by reapplying infrastructure into disposable environments instead of relying on extensive `tofu import`, unless critical shared resources must be retained.
- Document variable contracts (`.tf`, `.tfvars`) and guardrails so agents and engineers can compose changes quickly without re-learning resource schemas.

### Phase 3 – Agent Integration & Runtime Validation
- Replace `deploy_cloudformation_stack` and related helpers with the new OpenTofu wrapper, updating `sync_stack_identity` and deployment environments to surface workspace metadata alongside existing AWS credentials.
- Swap validation steps (lines ~2667-2759) to call `tofu validate`/`plan`, and tailor `BadPlan` messaging to highlight issues that block quick iteration (missing variables, diff conflicts, plan drift).
- Verify that generated code continues to call AWS services through boto3/SDKs without additional abstraction, running smoke deployments to ensure end-to-end workflows remain fast.

### Phase 4 – Prompt, Evaluation, and Workflow Acceleration
- Rewrite system/user prompts to teach the LLM how to reason about OpenTofu modules, variables, and outputs while emphasizing rapid iteration patterns (small diffs, focused plans).
- Adjust evaluation routines to parse OpenTofu plan/apply artifacts, marking iterations successful when applies complete cleanly and highlight actionable diffs when they do not.
- Update developer tooling (`make`, scripts) and documentation so engineers can reproduce agent flows locally with minimal friction.

### Phase 5 – Cutover & CloudFormation Decommissioning
- Execute the direct cutover: enable OpenTofu paths for all initiatives, measure deployment time improvements, and remove CloudFormation utilities and database columns tied to legacy logs.
- Backfill observability dashboards to visualize plan/apply duration and success rates, keeping iteration speed as a primary KPI.
- Finalize knowledge transfer (runbooks, onboarding guides) and archive or delete CloudFormation artifacts once the new pipeline is stable.

## Tooling & Automation Updates
- Extend CI pipelines to lint (`tofu fmt -check`) and validate modules; run `tofu plan -detailed-exitcode` for PR verification.
- Pre-provision AWS resources required for state backends (S3 bucket, DynamoDB table) and manage via a bootstrap OpenTofu module.
- Update local developer tooling commands (`make`, scripts) to include OpenTofu environment setup and wrappers.

## Risk & Mitigation
- **State Drift / Imports**: Risk of misaligned resources when importing. Mitigate with dry-run plans and manual verification before agent control.
- **LLM Familiarity**: Agents may struggle with new syntax. Mitigate via curated prompt examples, unit tests, and incremental rollout with human oversight.
- **Operational Parity**: Services relying on CloudFormation-specific events (stack policies, change sets) lose native features. Replace with OpenTofu workspaces, policy as code, and guardrails scripts.
- **Security**: Ensure state bucket encryption, IAM least privilege for the OpenTofu execution role, and secrets redaction from logs.

## Validation Strategy
- Unit tests for new utils: mock `subprocess` interactions to validate error parsing and retries.
- Integration smoke tests: run OpenTofu against a disposable sandbox environment and ensure outputs feed back into application logic (`get_stack_outputs` replacement).
- Agent regression tests: simulate plan/apply/evaluate loops with recorded OpenTofu outputs to verify that `GoalAchieved` conditions still trigger correctly.
- Observability checks: ensure metrics and logs previously tied to CloudFormation are recreated (or renamed) for OpenTofu pipelines.

## Rollout & Timeline (Indicative)
- **Weeks 1-2**: Phase 0 discovery, AWS-only spike, CLI packaging decision.
- **Weeks 3-4**: Phase 1 foundations—deliver OpenTofu wrapper, remote state scaffolding, and baseline automation metrics.
- **Weeks 5-6**: Phase 2 module migration for AWS services, ready for disposable environment applies.
- **Weeks 7-8**: Phase 3 agent integration and runtime validation with fast smoke deploys.
- **Weeks 9-10**: Phase 4 prompt/workflow updates, developer tooling alignment, and documentation refresh.
- **Weeks 11-12**: Phase 5 direct cutover, CloudFormation removal, and iteration-speed reporting.

## Resourcing & Collaboration
- Infrastructure engineer to lead module conversion and state backend setup.
- Autonomous agent engineer to adapt `self_driving_coder_agent` and prompts.
- Security/DevOps partner to review IAM posture and state storage configurations.
- CTO sponsorship for roadmap prioritization, change management, and vendor communication.

## Outstanding Questions
- Do any external systems consume the `cloudformation_logs` schema directly (dashboards, analytics)?
- Are there regulatory constraints mandating AWS-native tooling that could block OpenTofu adoption?
- What is the appetite for re-architecting existing stacks vs. maintaining functional parity during migration?
- Should we pursue multi-cloud capabilities immediately or focus on AWS parity first?
