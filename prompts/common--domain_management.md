## Domain Usage Contract

The system passes the task-specific subdomain 
- into CloudFormation as the required parameter `DomainName`.
- into the operating system environment under the environment variable name `DOMAIN_NAME`.

`DomainName` (CloudFormation) and `DOMAIN_NAME` (environment variable) are always identical and represent the single source of truth for the task-specific subdomain.

### Required wiring
- Any cloudformation service that needs a DNS name must reference `!Ref DomainName`.
- Any Python code (including automated tests) that needs a DNS name must retrieve it from os.getenv("DOMAIN_NAME").

### Prohibited
- **never** attempt to infer or generate parent business domains because parent domains are managed externally and not safe to assume in task-level stacks.
- **never** add parameters for the business root domain.
- **never** hardcode the domain name, not even in automated tests.  
    - The domain name is sandboxed per task, and so if the domain name is hardcoded the test will fail when run in other cloudformation stacks
    - If you see that python code or an automated test has hardcoded the domain name, you **must** modify it to use the dynamic value from the environment

### Assumptions
- DNS hosted zone and parent business domain are managed outside this template. Do not attempt to modify them here.

### Email Usage
- SES resources (such as receipt rules, MX/TXT/DKIM records, S3 actions, Lambda forwarders, etc.) **must** use the `DomainName` parameter for all SES-related DNS records and for constructing email addresses (e.g., `info@!Ref DomainName`).
- Stacks **must not** introduce or depend on the parent business domain for SES configurations.
- S3 buckets used for inbound email storage **must** be tied to the `DomainName` context (for example, by prefixing objects with the domain or a task identifier).
