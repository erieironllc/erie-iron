import logging

from django.core.management.base import BaseCommand, CommandError

from erieiron_autonomous_agent.models import Business, CloudAccount
from erieiron_autonomous_agent.utils.cloud_accounts import store_credentials_secret, _now
from erieiron_common.enums import CloudProvider, EnvironmentType

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Phase 2: Store cross-account credentials and create CloudAccount record (control plane operations only)"
    
    def add_arguments(self, parser):
        parser.add_argument('target_account_id', type=str, help='AWS account ID for target account')
        parser.add_argument('business_name', type=str, help='Name of the business to associate')
        parser.add_argument('env_type', type=str, choices=['dev', 'production'], help='Environment type')
        parser.add_argument('--role-arn', type=str, required=True, help='ARN of the created IAM role')
        parser.add_argument('--external-id', type=str, required=True, help='External ID for role assumption')
        parser.add_argument('--force', action='store_true', help='Force creation even if CloudAccount exists')
    
    def handle(self, *args, **options):
        target_account_id = options['target_account_id']
        business_name = options['business_name']
        env_type = options['env_type']
        role_arn = options['role_arn']
        external_id = options['external_id']
        force = options.get('force', False)
        
        try:
            # Validate inputs
            business = self._validate_business(business_name)
            self._validate_account_id(target_account_id)
            self._validate_role_arn(role_arn, target_account_id)
            
            # Check if CloudAccount already exists
            existing_cloud_account = self._check_existing_cloud_account(business, target_account_id, env_type, force)
            if existing_cloud_account and not force:
                self.stdout.write(self.style.WARNING(f'CloudAccount already exists: {existing_cloud_account}'))
                self.stdout.write(f'Use --force to recreate or choose different account/environment.')
                return
            
            self.stdout.write(f'Phase 2: Creating CloudAccount and storing credentials for {target_account_id}...')
            
            # Create or update CloudAccount record first
            if existing_cloud_account:
                self.stdout.write(f'Updating existing CloudAccount: {existing_cloud_account}')
                cloud_account = existing_cloud_account
                # Update metadata with new bootstrap information
                self.stdout.write(f'Updating CloudAccount metadata with new external_id: {external_id}')
                cloud_account.metadata.update({
                    "bootstrap_role_arn": role_arn,
                    "bootstrap_external_id": external_id,
                    "environment": env_type
                })
                cloud_account.save()
            else:
                self.stdout.write('Creating new CloudAccount record...')
                cloud_account = CloudAccount.objects.create(
                    business=business,
                    name=f"{business_name}-{env_type}",
                    provider=CloudProvider.AWS,
                    account_identifier=target_account_id,
                    metadata={
                        "bootstrap_role_arn": role_arn,
                        "bootstrap_external_id": external_id,
                        "environment": env_type
                    }
                )
            
            # Store credentials in Secrets Manager
            self.stdout.write('Storing cross-account credentials in Secrets Manager...')
            self.stdout.write(f'Credentials payload external_id: {external_id}')
            
            # Debug: Check current AWS credentials and region
            import boto3
            try:
                current_identity = boto3.client('sts').get_caller_identity()
                current_region = boto3.client('secretsmanager').meta.region_name
                self.stdout.write(f'Current AWS Identity: Account={current_identity["Account"]}, User/Role={current_identity["Arn"]}')
                self.stdout.write(f'Current AWS Region: {current_region}')
            except Exception as e:
                self.stdout.write(f'Failed to get AWS identity: {e}')
            
            # Debug: Show secret name being used
            secret_name = cloud_account.build_secret_name()
            self.stdout.write(f'Secret name being used: {secret_name}')
            
            credentials_payload = {
                "role_arn": role_arn,
                "external_id": external_id,
                "environment": env_type,
                "bootstrap_timestamp": _now().isoformat()
            }
            
            credentials_secret_arn = store_credentials_secret(cloud_account, credentials_payload)
            
            # Update CloudAccount with secret ARN
            cloud_account.credentials_secret_arn = credentials_secret_arn
            cloud_account.save()
            
            # Verify the secret was updated correctly
            self.stdout.write('Verifying secret was updated with correct external_id...')
            try:
                from erieiron_common import aws_utils
                stored_secret = aws_utils.get_secret(credentials_secret_arn)
                stored_external_id = stored_secret.get("external_id")
                if stored_external_id == external_id:
                    self.stdout.write(f'✓ Secret update verified: external_id matches ({external_id})')
                else:
                    self.stdout.write(f'✗ Secret update FAILED: stored external_id="{stored_external_id}" != expected="{external_id}"')
                    raise CommandError(f'Secret was not updated correctly with new external_id')
            except Exception as e:
                self.stdout.write(f'✗ Failed to verify secret update: {e}')
                raise CommandError(f'Could not verify secret was updated: {e}')
            
            # Set as default for the environment
            self.stdout.write(f'Setting as default {env_type} account...')
            if env_type == 'dev':
                cloud_account.set_default_flags(dev=True)
            else:
                cloud_account.set_default_flags(production=True)
            
            self.stdout.write(self.style.SUCCESS(
                f'Phase 2 completed successfully!\n'
                f'CloudAccount ID: {cloud_account.id}\n'
                f'Secret ARN: {credentials_secret_arn}\n'
                f'Set as default {env_type} account for business {business_name}'
            ))
            
        except Exception as e:
            logger.exception(f"Bootstrap Phase 2 failed: {e}")
            raise CommandError(f"Bootstrap Phase 2 failed: {e}")
    
    def _validate_business(self, business_name):
        """Validate that the business exists"""
        try:
            # First try exact match
            business = Business.objects.get(name=business_name)
            self.stdout.write(f'Found business: {business}')
            return business
        except Business.DoesNotExist:
            # Try case-insensitive match
            try:
                business = Business.objects.get(name__iexact=business_name)
                self.stdout.write(f'Found business (case-insensitive match): {business}')
                return business
            except Business.DoesNotExist:
                raise CommandError(f'Business "{business_name}" not found. Please create the business first.')
    
    def _validate_account_id(self, account_id):
        """Validate AWS account ID format"""
        if not account_id.isdigit() or len(account_id) != 12:
            raise CommandError(f'Invalid AWS account ID: {account_id}. Must be 12 digits.')
    
    def _validate_role_arn(self, role_arn, expected_account_id):
        """Validate role ARN format and account ID"""
        if not role_arn.startswith('arn:aws:iam::'):
            raise CommandError(f'Invalid role ARN format: {role_arn}')
        
        # Extract account ID from ARN
        try:
            arn_account_id = role_arn.split(':')[4]
            if arn_account_id != expected_account_id:
                raise CommandError(
                    f'Role ARN account ID {arn_account_id} does not match target account ID {expected_account_id}'
                )
        except (IndexError, ValueError):
            raise CommandError(f'Could not parse account ID from role ARN: {role_arn}')
    
    def _check_existing_cloud_account(self, business, target_account_id, env_type, force):
        """Check if CloudAccount already exists"""
        # First check if ANY CloudAccount exists for this account_identifier
        # This prevents creating duplicate CloudAccounts for the same AWS account
        try:
            existing = CloudAccount.objects.get(account_identifier=target_account_id)
            self.stdout.write(f'Found existing CloudAccount for account {target_account_id}: {existing.name} (business: {existing.business.name})')
            
            # If it belongs to a different business, warn but use it anyway
            if existing.business != business:
                self.stdout.write(self.style.WARNING(
                    f'CloudAccount belongs to different business: {existing.business.name} != {business.name}'
                ))
                self.stdout.write(f'Will use existing CloudAccount: {existing}')
            
            return existing
            
        except CloudAccount.DoesNotExist:
            return None