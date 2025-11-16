
## Background

Previously, Erie Iron used a two-stack model:
- **Foundation Stack** (`opentofu/foundation/stack.tf`): Persistent resources like RDS, SES, security groups
- **Application Stack** (`opentofu/application/stack.tf`): Application delivery components like ECS services, ALBs

## Migration Procedure

**Merge Foundation Resources into Application Stack**:
   - Copy all resource definitions from `foundation/stack.tf` to `application/stack.tf`
   - Merge variable definitions (avoid duplicates)
   - Combine locals sections
   - Consolidate outputs
   - Update resource references as needed

**Key Merge Considerations**:
   - Database resources: `aws_db_subnet_group`, `aws_db_instance`, `null_resource` for pgvector
   - Security group rules: Consolidate RDS ingress rules
   - Remove cross-stack dependencies (no more foundation outputs to application)
   - Update ECS task definitions to reference local database outputs

## Notes
- **do not** create a FoundationStackIdentifier parameter or variable.  Use StackIdentifier everywhere

## Success Criteria

Migration is complete when:
- [✅] Single APPLICATION stack contains all infrastructure resources
- [ ] No foundation stack state files remain active
