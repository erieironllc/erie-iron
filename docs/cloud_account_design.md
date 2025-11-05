# Cloud Account Model Overview

## Summary
- Introduced a first-class `CloudAccount` entity to map deployment credentials to a business.
- Each infrastructure stack now references an optional cloud account FK, allowing per-stack provider isolation.
- Credentials are stored outside the database via the existing AWS Secrets Manager helper, keyed under the business secret namespace.
- All IaC automation (OpenTofu) obtains credentials via a provider-aware adapter that caches STS sessions with logging for auditability.
- A new UI tab enables CRUD management of cloud accounts, including rotation of assume-role secrets and default environment selection.

## Model & Data Flow
- `CloudAccount` captures `business_id`, provider enum, display name, account identifier, metadata JSON (currently region hints), default flags (`dev`, `production`), and a pointer to the secret ARN.
- Secret payload schema (AWS v1) includes `role_arn`, optional `external_id`, optional `session_name`, and `session_duration` (validated as 900–43,200 seconds).
- Stacks created through `InfrastructureStack.get` auto-bind to the business default for the environment; existing stacks surface the association in both list and detail views.
- `cloud_accounts.build_aws_env` supplies per-stack environment variables by assuming the configured role and caching credentials with a five-minute refresh window for resilience.

## API & UI Notes
- `/api/business/<id>/cloud-accounts` (GET) returns serialized accounts plus provider choices.
- `/create`, `/<account_id>`, and `/<account_id>/delete` endpoints power Backbone interactions for create/update/delete with CSRF headers.
- The Backbone view defers to the server for fresh data after mutations to keep derived defaults in sync, and shields credentials behind an "update" toggle during edits.
- Business infrastructure tables now surface the associated cloud account, and stack detail pages link back to the management tab.

## Follow-up Ideas
1. Extend provider support (Azure / GCP) by layering provider-specific credential schemas and adapters, and expose conditional form controls per provider.
2. Allow stacks to reassign cloud accounts from the UI, with safeguards when tearing down resources.
3. Add background auditing to refresh or validate secrets proactively and alert when assume-role calls fail.
4. Implement secret deletion/rotation hooks when a cloud account is removed.
5. Persist cross-environment defaults (e.g., stage) and validate that at least one account covers each required deployment environment for production businesses.
