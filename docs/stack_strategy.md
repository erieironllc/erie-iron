# Stack Strategy Guide

## Overview

Stack strategy controls how infrastructure stacks are allocated for a business. This affects AWS costs, deployment isolation, and operational complexity.

## Strategies

### Single Stack
- **Stacks:** Production only
- **Behavior:** All coding and deployments happen in production
- **Isolation:** None
- **Cost:** Lowest (~$50-100/month)
- **Best for:**
  - Very early development
  - Pre-user testing
  - Prototyping
  - When you want absolute minimum infrastructure

### Production + Development
- **Stacks:** Production + one shared development
- **Behavior:**
  - All coding tasks run in shared dev stack
  - Production deployments go to production stack
  - All initiatives share the same dev stack
- **Isolation:** Production isolated from development
- **Cost:** Medium (~$100-200/month)
- **Best for:**
  - Active businesses with users
  - When you want cost control but need dev/prod separation
  - Sequential initiative development

### Per Initiative
- **Stacks:** Production + one stack per initiative
- **Behavior:**
  - Each initiative gets dedicated dev stack
  - Production deployments go to production stack
  - Full isolation between initiatives
- **Isolation:** Maximum - each initiative fully isolated
- **Cost:** Highest (~$200-400/month per initiative)
- **Best for:**
  - Complex businesses
  - Parallel initiative development
  - When isolation is critical
  - When cost is not primary concern

## Changing Strategies

### From Per Initiative → Prod + Dev
- Existing initiative stacks remain (not deleted)
- New tasks will use shared dev stack
- Consider manually cleaning up old initiative stacks

### From Prod + Dev → Single Stack
- Existing dev stack remains
- All new work goes to production
- **Warning:** No isolation between dev and prod

### From Single Stack → Prod + Dev or Per Initiative
- Dev stacks will be created as needed
- Safer transition (adding isolation)

## Technical Implementation

Stack selection is controlled by `InfrastructureStack.resolve_target_stack()` which:
1. Reads `business.stack_strategy`
2. Examines task type (production deployment vs development)
3. Returns appropriate (env_type, initiative) tuple
4. Agent uses this to get or create stack

## Cost Estimation

Based on typical AWS usage:

| Strategy | Monthly Cost |
|----------|-------------|
| Single Stack | ~$50-100 |
| Prod + Dev | ~$100-200 |
| Per Initiative (n=3) | ~$200-400 |

Costs vary based on:
- Database size/type
- Container count and size
- Data transfer
- Other AWS services

## Setting Stack Strategy

### Via UI

1. Navigate to Business edit page
2. Find "Stack Strategy" section
3. Select one of the three radio button options:
   - **Single Stack** - Minimal infrastructure, all work in production
   - **Production + Development** - Two stacks, dev/prod separation
   - **Per Initiative** - Maximum isolation, one stack per initiative
4. Click "Save Changes"

### Via Code

```python
from erieiron_autonomous_agent.models import Business
from erieiron_common.enums import StackStrategy

business = Business.objects.get(id=business_id)
business.stack_strategy = StackStrategy.PROD_AND_DEV
business.save()
```

## Stack Creation Rules

### Single Stack Strategy
- **Allowed:** Business-level production stack only
- **Forbidden:** Dev stacks, initiative stacks
- **Behavior:** All tasks execute in production stack regardless of task type

### Prod + Dev Strategy
- **Allowed:** Business-level production stack + business-level dev stack
- **Forbidden:** Initiative-scoped stacks
- **Behavior:** Coding tasks use shared dev stack, production deployments use prod stack

### Per Initiative Strategy
- **Allowed:** Business-level production stack + initiative-scoped dev stacks
- **Forbidden:** Business-level dev stacks (dev must be initiative-scoped)
- **Behavior:** Each initiative gets its own dev stack, production uses shared prod stack

## Validation and Error Handling

The system enforces stack strategy rules at stack creation time. Attempting to create an illegal stack will raise a `ValueError` with a descriptive message:

```python
# Example: Attempting to create initiative stack with PROD_AND_DEV strategy
ValueError: Business MyBusiness uses PROD_AND_DEV strategy.
Cannot create initiative-scoped stack. Use business-level dev stack instead.
```

## Migration Notes

When changing from `per_initiative` to another strategy:
- Existing initiative stacks are **not** automatically deleted
- They remain in your AWS account and continue to incur costs
- New tasks will not create new initiative stacks
- Consider manually reviewing and removing unused initiative stacks

## Code Examples

### Getting the Correct Stack for a Task

```python
from erieiron_autonomous_agent.models import InfrastructureStack, Task
from erieiron_common.enums import InfrastructureStackType

# Recommended approach - uses strategy resolver automatically
stack = InfrastructureStack.get_stack_for_task(
    task,
    InfrastructureStackType.APPLICATION
)

# Alternative - manual resolution
business = task.initiative.business
env_type, initiative_scope = InfrastructureStack.resolve_target_env_type(
    business,
    task,
    InfrastructureStackType.APPLICATION
)
stack = InfrastructureStack.get_stack(
    task.initiative,
    InfrastructureStackType.APPLICATION,
    env_type
)
```

### Checking Strategy Capabilities

```python
from erieiron_autonomous_agent.models import Business
from erieiron_common.enums import StackStrategy

business = Business.objects.get(id=business_id)
strategy = StackStrategy(business.stack_strategy)

if strategy.requires_production_only():
    print("All work happens in production - no dev isolation")

if strategy.allows_dev_stack():
    print("Business has a shared dev stack")

if strategy.allows_initiative_stacks():
    print("Initiatives get their own stacks")
```

### Getting Stack Information

```python
from erieiron_autonomous_agent.models import Business

business = Business.objects.get(id=business_id)
info = business.get_stack_strategy_info()

print(f"Strategy: {info['strategy_label']}")
print(f"Production stacks: {info['prod_stack_count']}")
print(f"Dev stacks: {info['dev_stack_count']}")
print(f"Initiative stacks: {info['initiative_stack_count']}")
print(f"Total stacks: {info['total_stacks']}")
```

## Architecture Decisions

### Why Business-Level Configuration?

Stack strategy is a business-level decision because:
1. **Cost Management:** Infrastructure costs are budgeted at the business level
2. **Operational Consistency:** All initiatives within a business should follow the same deployment pattern
3. **Simplicity:** Avoids complex per-initiative stack configuration
4. **Strategic Alignment:** Infrastructure topology aligns with business maturity and risk tolerance

### Why Not Per-Task Configuration?

Individual tasks don't choose their stacks because:
1. **Prevents Proliferation:** Avoids uncontrolled stack creation
2. **Predictable Costs:** Business owner knows exact infrastructure footprint
3. **Enforces Standards:** Ensures consistent deployment practices
4. **Centralized Control:** Business owner maintains infrastructure governance

### Default Strategy: Per Initiative

The default is `PER_INITIATIVE` to:
1. **Maintain Backward Compatibility:** Preserves existing behavior
2. **Maximize Safety:** Provides maximum isolation during development
3. **Avoid Production Accidents:** Prevents dev work from affecting production
4. **Support Migration:** Existing databases default to current behavior

## Troubleshooting

### Error: "Cannot create dev stack with SINGLE_STACK strategy"

**Cause:** Business is configured for `single_stack` but a task is trying to create a dev stack.

**Solution:** Either:
- Change business to `prod_and_dev` or `per_initiative` strategy
- Run the task in production (if appropriate)

### Error: "Cannot create initiative-scoped stack with PROD_AND_DEV strategy"

**Cause:** Business uses `prod_and_dev` but code is trying to create an initiative-specific stack.

**Solution:** Change business to `per_initiative` strategy if initiative isolation is required.

### Error: "Task has no initiative but business uses PER_INITIATIVE strategy"

**Cause:** Task is not associated with an initiative, but the business requires initiative-scoped stacks.

**Solution:** Either:
- Assign the task to an initiative
- Change business strategy to `prod_and_dev` or `single_stack`

## Best Practices

1. **Start Conservative:** Begin with `single_stack` or `prod_and_dev` to minimize costs
2. **Upgrade as Needed:** Move to `per_initiative` only when parallel development requires isolation
3. **Monitor Costs:** Track AWS spend and adjust strategy if costs exceed budget
4. **Clean Up:** Regularly review and delete unused stacks when changing strategies
5. **Document Decisions:** Record why you chose a particular strategy for your business
6. **Test Changes:** Use a test business to validate strategy changes before applying to production

## Future Enhancements

Potential future improvements to stack strategy management:
- Automatic cost estimation before strategy changes
- Migration wizard to consolidate stacks when downgrading
- Stack usage analytics to inform strategy decisions
- Auto-cleanup of unused initiative stacks
- Warnings in UI when changing from `per_initiative` with existing stacks
