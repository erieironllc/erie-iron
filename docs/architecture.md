# Erie Iron Architecture & Infrastructure Guide for Planning Agents

This document gives language model agents a concise map of Erie Iron's architecture so they can plan safe, high-impact changes. It focuses on how code, data, and infrastructure fit together, what surfaces are extensible, and where to look before modifying behavior.

## System Intent
- Erie Iron operates autonomous businesses through layered AI agents with human governance checkpoints.
- The platform must stay legal, ethical, and capital efficient while iterating quickly on new businesses and capabilities.
- Every code change should preserve observability, replayable workflows, and a clear rollback story for both application and infrastructure paths.

## High-Level Runtime Topology
- **Django Web Service (`manage.py runserver` / ECS task)**
  - Hosts the UI (`erieiron_ui`), API endpoints, and background jobs triggered by Django management commands.
  - Integrates with PostgreSQL, Redis (for caching and Pub/Sub), and AWS services via `erieiron_common`.
- **Message Processor / Task Runner**
  - Long-lived worker consuming Pub/Sub topics from `erieiron_common.message_queue`.
  - Executes autonomous tasks, orchestrates agent workflows, and emits status updates and telemetry.
- **Scheduled Agent Loops**
  - Board, portfolio, and business-level agents run on cadences via management commands or Lambda-style invocations.
  - Leverage `erieiron_autonomous_agent` workflows and share persistence models with the web service.
- **External Integrations**
  - AWS (CloudFormation/OpenTofu, ECS, S3, SES, etc.), third-party APIs for capability execution, and multiple LLM providers through adapters.

## Repository Layout Landmarks
- `erieiron_common/`
  - **Infrastructure helpers**: `aws_utils.py`, `cloudformation_utils.py`, `opentofu_*` modules abstract provider differences and handle logging, retries, and ARN/state normalization.
  - **LLM adapters**: `llm_apis/` wraps OpenAI, Claude, Gemini, DeepSeek, etc., with shared request/response schemas and rate-limit handling.
  - **Messaging**: `message_queue/` defines Pub/Sub topics, payload schemas, and fan-out helpers for cross-agent coordination.
  - **Chat engine & domain management**: handle conversation state, inboxes, domain/DNS lifecycles, SSL automation.
- `erieiron_autonomous_agent/`
  - **Agent orchestration**: `board_level_agents/`, `business_level_agents/`, and `coding_agents/` implement prompts, planners, and execution loops.
  - **State models**: `models.py` tracks businesses, initiatives, tasks, LLM requests, infrastructure stacks, and lessons learned.
  - **Workflow glue**: `workflow.py` and `system_agent_llm_interface.py` coordinate task decomposition, capability resolution, and escalation.
  - **Management commands**: under `management/commands/` for scheduled runs (portfolio reviews, builder loops, telemetry exports).
- `erieiron_ui/`
  - **Django app** exposing dashboards, agent status tabs, IaC logs, and business analytics.
  - **Frontend assets** in `js/` (Backbone views, collection stores) and `sass/` (Bootstrap-based styling) compiled via Gulp (`npm run compile-ui`).
- `aws/` & `cloudformation/`
  - Legacy CloudFormation templates, helper scripts, and deployment shell utilities for EC2/ECS, message processor, and alarms.
- `opentofu/`
  - OpenTofu/Terraform modules for foundation (network, shared services) and application (ECS services, task definitions, DNS).
  - Uses backends configured per environment; state pointers stored in `InfrastructureStack.stack_arn` for downstream lookups.
- `prompts/`
  - Canonical prompt library powering agents; new agent behaviors should extend these files to stay consistent.
- `tests/` and `*/tests`
  - Pytest suites paired with the code they cover. Add regression coverage alongside new logic.

## Data & Observability Surfaces
- **Database**: PostgreSQL with pgvector extension (embedding search). Key tables: businesses, initiatives, tasks, capabilities, LLM requests, infrastructure stacks, lesson logs.
- **Messaging**: Redis or equivalent backing store powering Pub/Sub topics defined in `erieiron_common.message_queue.enums`.
- **Logging**: Centralized via Python logging; when catching exceptions, always call `logging.exception(e)` to preserve stack traces.
- **Telemetry Hooks**: Business reports, daily summaries, and infrastructure deployment logs surface in the UI and via email notifications.

## Infrastructure Workflows
- **Provisioning Options**
  - *CloudFormation Path*: managed AWS stacks defined under `cloudformation/`, orchestrated by `cloudformation_utils.py` and legacy scripts.
  - *OpenTofu Path*: preferred path toggled by `SELF_DRIVING_IAC_PROVIDER`. Modules in `opentofu/` with helpers `opentofu_stack_manager.py` and `opentofu_helpers.py` managing workspace metadata and plan/apply logs.
- **Deployment Targets**
  - ECS services for the Django web app and message processor containers (Dockerfiles at repo root).
  - Lambda utilities for periodic maintenance (e.g., dashboard refreshers) under `aws/`.
- **Secrets & Config**
  - AWS Secrets Manager and Parameter Store handled via `aws_utils.py` wrappers.
  - Runtime config pulled through `erieiron_common.runtime_config` and Django settings modules.
- **State Tracking**
  - `InfrastructureStack` model captures stack identifiers, workspaces, and status to reconcile IaC operations with business entities.

## Change Planning Guidance for LLMs
- Start with module docs: many flows have dedicated markdown in `docs/` (e.g., `erie_iron_execution_flow.md`, capability pipeline notes). Reference them before altering orchestration.
- Understand which agent tier you are touching (board vs. business vs. capability) and update corresponding prompts plus workflow glue.
- Infrastructure edits must update both provider backends when possible (CloudFormation + OpenTofu) or document why a divergence is acceptable.
- When extending capabilities, ensure the Pub/Sub topic contract and payload schema changes propagate through publishers, consumers, and tests.
- UI changes should update the Backbone view, Django view, template, and any telemetry feed that powers the screen.
- Any new cross-service coordination requires: logging, retry/timeout strategy, and clear escalation hooks to human operators.

## Quick Reference for Key Entry Points
- **Agent entry commands**: `python manage.py run_portfolio_leader`, `run_business_ceo`, `run_task_decomposer` (examples under `erieiron_autonomous_agent/management/commands/`).
- **Capability execution**: `erieiron_common.message_queue.handlers` and downstream service modules.
- **IaC orchestration**: `erieiron_common.opentofu_stack_manager.OpenTofuStackManager` and `erieiron_common.cloudformation_utils.CloudFormationStackManager`.
- **UI dashboards**: Django views in `erieiron_ui/views.py` with corresponding templates in `erieiron_ui/templates/` and Backbone screens under `erieiron_ui/js/`.

With this map, an LLM can zero in on the right layer, inspect the adjacent prompts/tests, and design changes that maintain Erie Iron's reliability and governance expectations.