import logging
import secrets
import string
import textwrap

from django.core.management.base import BaseCommand, CommandError

import settings
from erieiron_autonomous_agent.models import Business, CloudAccount, InfrastructureStack
from erieiron_autonomous_agent.utils import cloud_accounts
from erieiron_common.enums import CloudProvider, EnvironmentType, InfrastructureStackType
from erieiron_common.opentofu_stack_manager import OpenTofuStackManager

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Bootstrap cross-account IAM role in target AWS account"
    
    def add_arguments(self, parser):
        parser.add_argument('target_account_id', type=str, help='AWS account ID for target account')
        parser.add_argument('business_name', type=str, help='Name of the business to associate')
        parser.add_argument('env_type', type=str, choices=['dev', 'production'], help='Environment type')
        parser.add_argument('--external-id', type=str, required=False, help='External ID for role assumption (auto-generated if not provided)')
        parser.add_argument('--force', action='store_true', help='Force bootstrap even if CloudAccount exists')
    
    def handle(self, *args, **options):
        target_account_id = options['target_account_id']
        business_name = options['business_name']
        env_type = options['env_type']
        external_id = options.get('external_id')
        force = options.get('force', False)
        
        try:
            # Validate inputs
            business = self._validate_business(business_name)
            self._validate_account_id(target_account_id)
            
            # Check if CloudAccount already exists
            existing_cloud_account = self._check_existing_cloud_account(business, target_account_id, env_type, force)
            if existing_cloud_account and not force:
                self.stdout.write(self.style.WARNING(f'CloudAccount already exists: {existing_cloud_account}'))
                self.stdout.write(f'Use --force to recreate or choose different account/environment.')
                return
            
            # Generate external ID if not provided
            if not external_id:
                external_id = self._generate_external_id()
            
            self.stdout.write(f'Bootstrapping target account {target_account_id} for business {business_name} ({env_type})...')
            
            # Create Infrastructure Stack record
            stack = self._create_infrastructure_stack(business, target_account_id, env_type, external_id)
            
            # Deploy OpenTofu stack
            deployment_result = self._deploy_opentofu_stack(stack)
            
            # Extract outputs
            role_arn = deployment_result.outputs['role_arn']['value']
            external_id_output = deployment_result.outputs['external_id']['value']
            
            # Create or update CloudAccount and store credentials
            cloud_account = self._create_cloud_account(business, target_account_id, env_type, existing_cloud_account)
            self._store_credentials(cloud_account, role_arn, external_id_output)
            
            # Update stack with cloud account reference
            stack.cloud_account = cloud_account
            stack.save()
            
            self.stdout.write(self.style.SUCCESS(f'Bootstrap completed successfully!'))
            self.stdout.write(f'CloudAccount: {cloud_account}')
            self.stdout.write(f'Role ARN: {role_arn}')
            self.stdout.write(f'Credentials stored in Secrets Manager')
            
        except Exception as e:
            logger.exception("Bootstrap target account failed")
            raise CommandError(f"Bootstrap failed: {str(e)}")
    
    def _validate_business(self, business_name: str) -> Business:
        """Validate business exists in database."""
        try:
            # First try exact match
            business = Business.objects.get(name=business_name)
            logger.info(f"Found business: {business}")
            return business
        except Business.DoesNotExist:
            # Try case-insensitive match
            try:
                business = Business.objects.get(name__iexact=business_name)
                logger.info(f"Found business (case-insensitive match): {business}")
                return business
            except Business.DoesNotExist:
                raise CommandError(f"Business '{business_name}' not found in database")
    
    def _validate_account_id(self, account_id: str) -> None:
        """Validate AWS account ID format."""
        if not account_id.isdigit() or len(account_id) != 12:
            raise CommandError(f"Invalid AWS account ID format: {account_id} (must be 12 digits)")
    
    def _check_existing_cloud_account(self, business: Business, target_account_id: str, env_type: str, force: bool) -> CloudAccount:
        """Check if CloudAccount already exists."""
        try:
            return CloudAccount.objects.get(
                business=business,
                account_identifier=target_account_id,
                provider=CloudProvider.AWS
            )
        except CloudAccount.DoesNotExist:
            return None
    
    def _generate_external_id(self) -> str:
        """Generate secure random external ID."""
        alphabet = string.ascii_letters + string.digits
        external_id = ''.join(secrets.choice(alphabet) for _ in range(32))
        logger.info("Generated secure external ID for role assumption")
        return external_id
    
    def _create_infrastructure_stack(self, business: Business, target_account_id: str, env_type: str, external_id: str) -> InfrastructureStack:
        """Create Infrastructure Stack record for bootstrap."""
        env_type_enum = EnvironmentType(env_type)
        stack_namespace_token = f"{business.name}-{env_type}-bootstrap-{target_account_id}"
        
        stack = InfrastructureStack.objects.create(
            business=business,
            initiative=None,  # Bootstrap not tied to specific initiative
            stack_type=InfrastructureStackType.TARGET_ACCOUNT_BOOTSTRAP,
            env_type=env_type_enum,
            stack_namespace_token=stack_namespace_token,
            stack_vars={
                'control_plane_account_id': settings.AWS_ACCOUNT_ID,
                'external_id': external_id,
                'business_name': business.name,
                'target_account_id': target_account_id,
                'env_type': env_type
            }
        )
        
        logger.info(f"Created infrastructure stack: {stack.id}")
        return stack
    
    def _deploy_opentofu_stack(self, stack: InfrastructureStack):
        """Deploy OpenTofu stack using OpenTofuStackManager."""
        self.stdout.write('Deploying OpenTofu stack...')
        
        container_env = self._build_stack_environment(stack)
        manager = OpenTofuStackManager(stack, container_env=container_env or None)
        deployment_result = manager.apply()
        
        if not deployment_result or not hasattr(deployment_result, 'outputs'):
            raise CommandError("OpenTofu deployment failed or missing outputs")
        
        required_outputs = ['role_arn', 'external_id']
        for output in required_outputs:
            if output not in deployment_result.outputs:
                raise CommandError(f"Missing required output: {output}")
        
        logger.info("OpenTofu stack deployed successfully")
        return deployment_result
    
    def _create_cloud_account(self, business: Business, target_account_id: str, env_type: str, existing_cloud_account: CloudAccount = None) -> CloudAccount:
        """Create or update CloudAccount database record."""
        env_type_enum = EnvironmentType(env_type)
        
        if existing_cloud_account:
            cloud_account = existing_cloud_account
            self.stdout.write(f'Updating existing CloudAccount: {cloud_account}')
        else:
            cloud_account = CloudAccount.objects.create(
                business=business,
                name=f"{business.name}-{env_type}",
                provider=CloudProvider.AWS,
                account_identifier=target_account_id,
                is_default_dev=(env_type == 'dev'),
                is_default_production=(env_type == 'production')
            )
            self.stdout.write(f'Created new CloudAccount: {cloud_account}')
        
        # Update default flags to ensure only one default per environment
        cloud_account.set_default_flags(
            dev=(env_type == 'dev'),
            production=(env_type == 'production')
        )
        
        logger.info(f"CloudAccount configured: {cloud_account.id}")
        return cloud_account
    
    def _store_credentials(self, cloud_account: CloudAccount, role_arn: str, external_id: str) -> None:
        """Store role credentials in AWS Secrets Manager."""
        self.stdout.write('Storing credentials in Secrets Manager...')
        
        secret_payload = {
            'role_arn': role_arn,
            'external_id': external_id,
            'session_name': f'erieiron-{cloud_account.account_identifier}',
            'session_duration': 3600
        }
        
        credentials_secret_arn = cloud_accounts.store_credentials_secret(cloud_account, secret_payload)
        cloud_account.credentials_secret_arn = credentials_secret_arn
        cloud_account.save()
        logger.info(
            "Stored target account credentials",
            extra={
                "cloud_account_id": str(cloud_account.id),
                "account_identifier": cloud_account.account_identifier,
            },
        )
        
    def _build_stack_environment(self, stack: InfrastructureStack) -> dict:
        """Build AWS env vars so OpenTofu executes with the assumed target role."""
        try:
            env = cloud_accounts.build_cloud_credentials([stack])
            if env:
                self.stdout.write('Using target account credentials for OpenTofu execution...')
            return env
        except Exception as exc:
            logger.warning(
                "Falling back to ambient AWS credentials for stack %s: %s",
                stack.id,
                exc,
                exc_info=True,
            )
        return {}
