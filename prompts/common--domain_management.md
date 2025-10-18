## Domain Usage Contract

**Immediate policy reminder:** Domain/DNS/Route53/ACM/SES domain aliasing edits are forbidden for this iteration. If any requirement in this contract would require changing those resources, you must emit `{ "blocked": { "category": "infra_boundary", "reason": "Domain/DNS edits are forbidden for this iteration. Any Route53/ACM/DomainName/SES domain aliasing changes must be handled by the orchestration layer." } }` instead of planning a code edit.

The system passes the task-specific subdomain 
- into CloudFormation as the required parameter `DomainName`.
- into the operating system environment under the environment variable name `DOMAIN_NAME`.


### Documentation Examples Policy

- Documentation and examples must show DOMAIN_NAME as a dynamic value, not a hardcoded literal.
- Prefer one of:
  - A parameterized form that uses the exact variable name: https://{DOMAIN_NAME}/unsubscribe?token=...
  - Or a dual-format approach: provide both a dynamic template and, if tests require a literal, include a clearly marked example line generated from the current DOMAIN_NAME provided by the evaluator.
- Never hardcode a specific domain (e.g., task-design-forward-digest-minimal-ui.articleparse.com) unless the evaluator explicitly requires a verbatim literal for a test, and include both forms if doing so.

### Required wiring
- Any cloudformation service that needs a DNS name must reference `!Ref DomainName`. The orchestration layer is responsible for ensuring this reference already exists; do **not** plan edits that would alter DNS wiring.
- When you publish DNS for the application, create `AWS::Route53::RecordSet` alias records (`Type: A` and optionally `AAAA`) that point to the ALB via `AliasTarget`. Operational automation outside this iteration maintains these records; if the stack is missing them, emit the `infra_boundary` blocked payload rather than proposing edits.
- Any Python code (including automated tests) that needs a DNS name must retrieve it from os.getenv("DOMAIN_NAME").

### Prohibited
- **never** attempt to infer or generate parent business domains because parent domains are managed externally and not safe to assume in task-level stacks.
- **never** add parameters for the business root domain.
- **never** hardcode the domain name, not even in automated tests.  
    - The domain name is sandboxed per task, and so if the domain name is hardcoded the test will fail when run in other cloudformation stacks
    - If you see that python code or an automated test has hardcoded the domain name, you **must** modify it to use the dynamic value from the environment
- **never** create new Route53 hosted zones for task-specific subdomains. Reuse either the business's existing hosted zone (when the wildcard certificate is available) or the Erie Iron fallback zone `erieironllc.com`.

### Assumptions
- Domain is hosted in Route53 in the same AWS account, and DNS records must be created by CloudFormation.

### Email Usage
- SES resources (such as receipt rules, MX/TXT/DKIM records, S3 actions, Lambda forwarders, etc.) **must** use the `DomainName` parameter for all SES-related DNS records and for constructing email addresses (e.g., `info@!Ref DomainName`).
- Automated SES smoke or integration tests must send from an address built off the task DomainName (for example, `noreply@{os.getenv("DOMAIN_NAME")}`) and **must** target the SES Mailbox Simulator (`success@simulator.amazonses.com` for a happy-path delivery, with `bounce@simulator.amazonses.com`, `complaint@simulator.amazonses.com`, `ooto@simulator.amazonses.com`, or `suppressionlist@simulator.amazonses.com` when specifically validating those flows). Never send test traffic to real recipients or to DomainName mailboxes.
- The self-driving deployment agent handles SES domain verification, DKIM, and MX automation. Do not add manual verification steps or custom automation—assume the helper keeps the identity verified and surface a clear failure only if AWS still reports it unverified.
- Stacks **must not** introduce or depend on the parent business domain for SES configurations.
- S3 buckets used for inbound email storage **must** be tied to the `DomainName` context (for example, by prefixing objects with the domain or a task identifier).
- CloudFormation will create all SES-required DNS records via Route53 record sets.
