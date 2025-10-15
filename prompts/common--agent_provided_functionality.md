## Automation-Provided Stack Helpers
The self-driving deployment agent already manages several infrastructure guardrails for every stack update. The self-driving agent:
- builds CloudFormation parameter sets
- disables Lambda reserved concurrency before updates
- manages SES domain verification
- checks SES quotas, and cleans up wedged stacks or buckets.

**Never never never** plan architecture, tasks, or edits that attempt to recreate, modify, or test any of the above behaviors
    - If a failure points at these helpers, record the observation and plan only the application changes needed around them, or return 'blocked'
