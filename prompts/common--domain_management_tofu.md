## Domain Usage Contract

### Canonical Inputs
- OpenTofu receives the task-specific subdomain via the required `DomainName` parameter.
- Runtime code receives the same value through the `DOMAIN_NAME` environment variable.

Both values are supplied by the orchestration layer. Never invent or transform them into a parent/apex domain; always use them verbatim.

### When planners may edit DNS resources
Planners may adjust Route53/ACM/SES resources **only** when those resources already exist in the template **and** every property derives from `!Ref DomainName`. Typical allowed edits:

| Allowed edit | Example |
| --- | --- |
| Update an existing ALB alias record | Modify `AppAliasRecord` so `AliasTarget.DNSName` references the new ALB logical ID while keeping `Name: !Ref DomainName`. |
| Fix SES verification records | Correct the TXT/CNAME value for the SES identity resource that already references `!Ref DomainName`. |
| Add missing DependsOn/conditions | Attach `DependsOn` between DNS records and the resources they validate so OpenTofu recreates them deterministically. |

Any change outside of these scenarios (new hosted zones, records for a different domain, manual CLI steps) is out of scope and must be blocked.

### Blocked operations (emit `{ "blocked": { "category": "infra_boundary", ... } }`)
- Creating or modifying hosted zones, apex domains, wildcard certificates, or SES identities for domains other than `DomainName`.
- Adding Route53 records that hardcode literal domains or depend on parent business domains.
- Requesting DNS changes that cannot be applied via the existing OpenTofu templates.

### Documentation and example policy
- Documentation and inline comments must use dynamic placeholders (`https://{DOMAIN_NAME}/unsubscribe`) or clearly mark literal examples as "Example using DOMAIN_NAME=...".
- If tests require an explicit literal, include both the placeholder form and a single example derived from evaluator logs.

### Runtime wiring requirements
- Any OpenTofu resource that needs a DNS name must source it from `!Ref DomainName`.
- When publishing DNS for the app, Route53 alias records (`Type: A` and optional `AAAA`) must point to the ALB via `AliasTarget`. If the record is missing entirely, block the plan and escalate instead of inventing new DNS resources.
- Python/tests must call `os.getenv("DOMAIN_NAME")` rather than inlining hostnames.

### Email usage
- SES resources (receipt rules, MX/TXT/DKIM records, forwarding Lambdas) must compose addresses using `!Ref DomainName` / `os.getenv("DOMAIN_NAME")`.
- OpenTofu stacks (`infrastructure.yaml` and `infrastructure-application.yaml`) must provision the SES receipt rule set and rules needed for inbound email, using names derived from `!Ref StackIdentifier` and `!Ref DomainName`.
- The same templates must include a custom resource (e.g., `Custom::ActivateSesRuleSet`) that calls `ses:SetActiveReceiptRuleSet` so the stack-owned rule set becomes active after creation and clears back to `""` on deletion.
- Automated tests must send from `{DOMAIN_NAME}` addresses to the SES mailbox simulator (never to real inboxes).
- When a task includes email receipt handling, at least one acceptance test must send an email through SES and assert that the configured receipt rule delivers it to the expected downstream target (S3, Lambda, SNS, etc.) so the rule is exercised end to end.
- The deployment agent owns SES domain verification. Do not add bespoke verification scripts; just surface clear diagnostics if AWS still reports the identity as unverified.
- S3 buckets that store inbound mail must namespace keys by DomainName or the stack identifier.

### Quick reference
- **Allowed:** editing OpenTofu DNS records that already exist and stay within the DomainName boundary.
- **Blocked:** introducing new DNS resources or manual steps outside OpenTofu.
- Always describe DomainName-driven behavior in documentation and code, never literal domains.
