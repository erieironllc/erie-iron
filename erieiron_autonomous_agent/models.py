import copy
import difflib
# Load model once at startup
import json
import logging
import os
import subprocess
import tempfile
import textwrap
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta, datetime
from pathlib import Path
from typing import Tuple, Optional, Any

import boto3
from django.db import models, transaction
from django.db.models import Sum, Q, QuerySet
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.utils import timezone
from pgvector.django import VectorField

import settings
from erieiron_autonomous_agent.enums import (
    BusinessStatus,
    BusinessGuidanceRating,
    TrafficLight,
    TaskStatus,
    BusinessOperationType,
)
from erieiron_common import common
from erieiron_common.enums import (
    Level,
    LlcStructure,
    TaskExecutionSchedule,
    InitiativeType,
    GoalStatus,
    BusinessIdeaSource,
    TaskType,
    PubSubMessageType,
    EnvironmentType,
    InfrastructureStackType,
    StackStrategy,
    DEV_STACK_TOKEN_LENGTH,
    LlmVerbosity,
    CloudProvider, CredentialService, LlmModel, CredentialServiceProvisioning, TaskImplementationPhase,
    TaskImplementationSourceKind,
    IterationMode,
)
from erieiron_common.git_utils import GitWrapper
from erieiron_common.json_encoder import ErieIronJSONEncoder
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import BaseErieIronModel


@dataclass(frozen=True)
class TaskExecutionEvaluationResult:
    score: float
    metadata: dict[str, Any]


class Business(BaseErieIronModel):
    name = models.TextField(unique=True)
    source = models.TextField(null=False, choices=BusinessIdeaSource.choices())
    status = models.TextField(
        default=BusinessStatus.IDEA, choices=BusinessStatus.choices()
    )
    operation_type = models.TextField(
        default=BusinessOperationType.ERIE_IRON_AUTONOMOUS,
        choices=BusinessOperationType.choices(),
    )
    
    service_token = models.TextField(null=True)
    summary = models.TextField(null=True)
    raw_idea = models.TextField(null=True)
    bank_account_id = models.TextField(null=True)
    business_plan = models.TextField(null=True)
    architecture = models.TextField(null=True)
    ui_design_spec = models.TextField(null=True)
    value_prop = models.TextField(null=True)
    revenue_model = models.TextField(null=True)
    audience = models.TextField(null=True)
    niche_category = models.TextField(
        null=True,
        blank=True,
        help_text="Niche category used to generate this business idea (e.g., local_service_arbitrage)"
    )
    required_credentials = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    credential_arns = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    core_functions = models.JSONField(default=list)
    execution_dependencies = models.JSONField(default=list)
    business_finder_output = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    growth_channels = models.JSONField(default=list)
    personalization_options = models.JSONField(default=list)
    allow_autonomous_shutdown = models.BooleanField(default=True)
    needs_domain = models.BooleanField(default=False)
    web_container_cpu = models.PositiveIntegerField(default=512)
    web_container_memory = models.PositiveIntegerField(default=1024)
    web_desired_count = models.PositiveIntegerField(default=1)
    autonomy_level = models.TextField(null=True, choices=Level.choices())
    stack_strategy = models.TextField(
        default=StackStrategy.PER_INITIATIVE,
        choices=StackStrategy.choices(),
        help_text="Determines how infrastructure stacks are allocated for this business"
    )
    domain = models.TextField(null=True)
    domain_certificate_arn = models.TextField(null=True)
    route53_hosted_zone_id = models.TextField(null=True, blank=True)
    github_repo_url = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def get_required_credentials(self) -> list[CredentialService]:
        return [
            CredentialService(c)
            for c in common.ensure_list(self.required_credentials)
            if bool(CredentialService.valid_or(c))
        ]
    
    @property
    def required_credentials_csv(self) -> str:
        """Return required_credentials as a comma-delimited string for form display"""
        if not self.required_credentials:
            return ""
        credentials_list = common.ensure_list(self.required_credentials)
        return ", ".join(credentials_list)
    
    @property
    def stack_strategy_enum(self) -> StackStrategy:
        """Return stack_strategy as StackStrategy enum for template access"""
        return StackStrategy(self.stack_strategy)
    
    @staticmethod
    def get_portfolio_business() -> QuerySet['Business']:
        return Business.objects.exclude(id=Business.get_erie_iron_business().id)
    
    def get_llm_data(self):
        business_analysis, legal_analysis = self.get_latest_analysist()
        return {
            "business_id": self.id,
            "name": self.name,
            "niche_category": self.niche_category,
            "summary": self.summary,
            "business_plan": self.business_plan,
            "value_prop": self.value_prop,
            "core_functions": self.core_functions,
            "revenue_model": self.revenue_model,
            "audience": self.audience,
            "human_work": common.get_dict(self.businesshumanjobdescription_set.all()),
            "critical_evaluations": common.get_dict(self.businesssecondopinionevaluation_set.all().order_by("-timestamp")),
            'business_analysis': common.get_dict(business_analysis),
            'legal_analysis': common.get_dict(legal_analysis)
        }
    
    def get_domain_manager(self, cloud_account=None):
        from erieiron_common.domain_manager import DomainManager
        return DomainManager(self, cloud_account)
    
    def get_stack_strategy_info(self) -> dict:
        """
        Returns information about current stack strategy and existing stacks.
        Useful for UI warnings when changing strategies.
        """
        strategy = StackStrategy(self.stack_strategy)
        
        # Count existing stacks
        prod_stacks = self.infrastructurestack_set.filter(
            env_type=EnvironmentType.PRODUCTION
        ).count()
        dev_stacks = self.infrastructurestack_set.filter(
            env_type=EnvironmentType.DEV,
            initiative__isnull=True
        ).count()
        initiative_stacks = self.infrastructurestack_set.filter(
            env_type=EnvironmentType.DEV,
            initiative__isnull=False
        ).count()
        
        return {
            'strategy': strategy.value,
            'strategy_label': strategy.label(),
            'prod_stack_count': prod_stacks,
            'dev_stack_count': dev_stacks,
            'initiative_stack_count': initiative_stacks,
            'total_stacks': prod_stacks + dev_stacks + initiative_stacks,
            'allows_dev_stack': strategy.allows_dev_stack(),
            'allows_initiative_stacks': strategy.allows_initiative_stacks(),
            'requires_production_only': strategy.requires_production_only(),
        }
    
    def get_existing_required_credentials_llmm(self) -> list[LlmMessage]:
        return LlmMessage.user_from_data(
            "Existing Required Credentials.  Use for reference.  Not need to re-specify.",
            {"required_credentials": self.required_credentials or {}},
        )
    
    def llm_data(self):
        business_analysis, legal_analysis = self.get_latest_analysist()
        return {
            "summary": self.summary,
            "business_plan": self.business_plan,
            "value_prop": self.value_prop,
            "revenue_model": self.revenue_model,
            "audience": self.audience,
            "required_credentials": self.required_credentials,
            "core_functions": self.core_functions,
            "execution_dependencies": self.execution_dependencies,
            "growth_channels": self.growth_channels,
            "personalization_options": self.personalization_options,
            "autonomy_level": self.autonomy_level,
            "kpis": [kpi.description for kpi in self.businesskpi_set.all()],
            "goal": [goal.description for goal in self.businessgoal_set.all()],
            "business_analysis": (
                business_analysis.summary if business_analysis else None
            ),
        }
    
    def get_iam_role_name(self):
        return f"erieiron-{self.service_token}-role"
    
    def get_latest_board_guidance(self):
        return (
            BusinessGuidance.objects.filter(business=self)
            .order_by("created_timestamp")
            .last()
        )
    
    def get_latest_capacity(self):
        return (
            BusinessCapacityAnalysis.objects.filter(business=self)
            .order_by("created_timestamp")
            .last()
        )
    
    def get_latest_second_opinion(self, ) -> "BusinessSecondOpinionEvaluation":
        return BusinessSecondOpinionEvaluation.objects.filter(business=self).order_by("timestamp").last()
    
    def get_latest_analysist(
            self,
    ) -> tuple["BusinessAnalysis", "BusinessLegalAnalysis"]:
        return (
            BusinessAnalysis.objects.filter(business=self)
            .order_by("created_timestamp")
            .last(),
            BusinessLegalAnalysis.objects.filter(business=self)
            .order_by("created_timestamp")
            .last(),
        )
    
    @staticmethod
    def get_erie_iron_business() -> "Business":
        return Business.objects.get_or_create(
            name="Erie Iron, LLC", defaults={"source": BusinessIdeaSource.HUMAN}
        )[0]

    def get_application_repo_url(self) -> str:
        repo_url = common.default_str(self.github_repo_url).strip()
        if not repo_url:
            raise ValueError(f"application repo url is not configured for business {self.name}")
        return repo_url
    
    def needs_bank_balance_update(self):
        return not BusinessBankBalanceSnapshot.objects.filter(
            business=self, created_timestamp__gt=common.get_now() - timedelta(days=1)
        ).exists()
    
    def needs_capacity_analysis(self):
        """
        Returns True if no BusinessCapacityAnalysis has been created in the past 1 hours.
        """
        return not BusinessCapacityAnalysis.objects.filter(
            business=self, created_timestamp__gt=common.get_now() - timedelta(hours=1)
        ).exists()
    
    def needs_analysis(self):
        """
        Returns True if no BusinessAnalysis has been created in the past 14 days.
        """
        return not BusinessAnalysis.objects.filter(
            business=self, created_timestamp__gt=common.get_now() - timedelta(days=14)
        ).exists()
    
    def get_human_capacity(self):
        return {
            "timestamp": "2025-06-10T18:00:00Z",
            "active_humans": [
                {
                    "id": "jj",
                    "name": "JJ",
                    "available_hours_per_week": 10,
                    "current_task_load": 0,
                    "tasks_pending": 0,
                    "status": "HAS_CAPACITY",
                }
            ],
            "total_human_capacity_hours": 18,
            "total_pending_task_hours": 0,
            "capacity_utilization_percent": 0,
        }
    
    def get_new_business_budget_capacity(self):
        bank_balance = (
            BusinessBankBalanceSnapshot.objects.filter(business=self)
            .order_by("created_timestamp")
            .last()
        )
        if not bank_balance:
            return {"status": "bank balance is unknown"}
        else:
            return bank_balance.get_aggregate_balance_data(0.5)
    
    def get_budget_capacity(self):
        bank_balance = (
            BusinessBankBalanceSnapshot.objects.filter(business=self)
            .order_by("created_timestamp")
            .last()
        )
        if not bank_balance:
            return {"status": "bank balance is unknown"}
        else:
            return bank_balance.get_aggregate_balance_data()
        
        # todo make this real
        # return {
        #     "timestamp": "2025-06-11T18:00:00Z",
        #     "cash_on_hand_usd": 12432.75,
        #     "monthly_burn_rate_usd": 2800.00,
        #     "runway_months": 4.4,
        #     "committed_monthly_expenses": {
        #         "aws": 700,
        #         "contractors": 1200,
        #         "software_tools": 300,
        #         "other": 600
        #     },
        #     "forecasted_revenue_usd": {
        #         "next_30_days": 350.00,
        #         "next_90_days": 1100.00
        #     },
        #     "available_budget_for_new_investments_usd": 2000.00
        # }
    
    def get_aws_capacity(self, fake_aws=False):
        if not fake_aws:
            return {
                "compute": "virtually unlimited",
                "storage": "virtually unlimited",
            }
        
        return {
            "timestamp": "2025-06-10T18:00:00Z",
            "region": "us-west-2",
            "compute": {
                "ec2_instances_available": {
                    "t3.medium": 20,
                    "g4dn.xlarge": 2,
                    "c5.large": 10,
                },
                "autoscaling_groups": [
                    {
                        "name": "collaya-inference-asg",
                        "desired_capacity": 2,
                        "max_capacity": 5,
                        "current_capacity": 2,
                    }
                ],
                "lambda_invocations_per_minute": {"limit": 1000, "current_usage": 540},
                "ecs_clusters": [
                    {
                        "name": "collaya-prod",
                        "running_tasks": 12,
                        "available_capacity_percent": 40,
                    }
                ],
            },
            "gpu": {
                "available_gpus": {"g4dn.xlarge": 2, "p3.2xlarge": 0},
                "inference_queue_length": 3,
                "expected_wait_time_seconds": 120,
            },
            "storage": {
                "s3": {"total_objects": 120000, "total_size_gb": 310.4},
                "ebs": {"total_volumes": 12, "used_storage_gb": 240},
                "efs": {"used_storage_gb": 38},
            },
            "network": {
                "api_gateway": {
                    "requests_per_second": {"limit": 5000, "current_usage": 1300}
                },
                "bandwidth_mbps": {"ingress": 220, "egress": 180},
            },
            "cost": {
                "month_to_date_spend_usd": 342.18,
                "forecasted_monthly_spend_usd": 720,
                "budget_limit_usd": 1000,
            },
            "alerts": [
                "Low GPU availability in us-west-2",
                "Lambda usage > 50% of quota",
                "S3 nearing 90% of cost allocation warning threshold",
            ],
        }
    
    def get_kpis_status(self):
        kpis = BusinessKPI.objects.filter(business=self).order_by("created_timestamp")
        if not kpis.exists():
            kpis_status = {"summary": "No KPIs defined"}
        else:
            kpis_status = {
                "summary": "\n".join(
                    f"{k.name} (kpi_id={k.kpi_id}): latest value = {BusinessKPIProgress.objects.filter(kpi=k).order_by('-created_timestamp').first().value if BusinessKPIProgress.objects.filter(kpi=k).exists() else 'N/A'}"
                    for k in kpis
                )
            }
        
        return kpis_status
    
    def get_goals_status(self):
        goals = BusinessGoal.objects.filter(business=self).order_by("created_timestamp")
        if not goals.exists():
            goals_status = {"summary": "No Goals defined"}
        else:
            goals_status = {
                "summary": "\n".join(
                    f"{g.goal_id} (kpi_id={g.kpi.kpi_id}): {g.status}" for g in goals
                )
            }
        
        return goals_status
    
    def snapshot_code(
            self,
            self_driving_task_iteration: "SelfDrivingTaskIteration"
    ):
        instructions = common.get(
            self_driving_task_iteration, ["evaluation_json", "instructions"]
        )
        sandbox_path = Path(self_driving_task_iteration.self_driving_task.sandbox_path)
        
        files_to_index = list(
            common.iterate_files_deep(
                sandbox_path,
                file_extensions=[
                    ".py",
                    ".html",
                    ".js",
                    ".css",
                    ".scss",
                    ".yaml",
                    ".sh",
                    ".txt",
                    "Dockerfile",
                ],
                gitignore_patterns=["core/migrations/"],
            )
        )
        
        for relative_file_path in common.strings(files_to_index):
            code_file = CodeFile.get(self, relative_file_path)
            version = code_file.get_latest_version()
            
            if not version:
                code_file.init_from_codefile(
                    self_driving_task_iteration, relative_file_path
                )
            else:
                if (sandbox_path / relative_file_path).read_text() != version.code:
                    CodeFile.update_from_path(
                        self_driving_task_iteration,
                        (sandbox_path / relative_file_path),
                        instructions,
                    )
    
    def get_secrets_root_key(self, env_type: EnvironmentType):
        from erieiron_common import aws_utils
        env_type = EnvironmentType(env_type)
        
        project_name = aws_utils.sanitize_aws_name(self.service_token, max_length=64)
        return f"z/{project_name}/{env_type.value}"
    
    def get_default_cloud_account(
            self,
            env_type: EnvironmentType = None
    ) -> "CloudAccount | None":
        qs = self.cloud_accounts.all()
        
        if not env_type:
            default_prod = qs.filter(is_default_production=True).first()
            if default_prod:
                return default_prod
            if not qs.exists():
                return self._ensure_default_cloud_account(EnvironmentType.PRODUCTION)
            return qs.filter(is_default_dev=True).first()
        elif EnvironmentType.PRODUCTION.eq(env_type):
            default_prod = qs.filter(is_default_production=True).first()
            if default_prod:
                return default_prod
            if not qs.exists():
                return self._ensure_default_cloud_account(EnvironmentType.PRODUCTION)
        elif EnvironmentType.DEV.eq(env_type):
            return qs.filter(is_default_dev=True).first()
        
        return None
    
    def iter_cloud_accounts(self) -> models.QuerySet:
        return self.cloud_accounts.order_by("name")

    def _ensure_default_cloud_account(
            self,
            env_type: EnvironmentType
    ) -> "CloudAccount | None":
        if EnvironmentType.PRODUCTION.neq(env_type):
            return None
        template_business = Business.get_erie_iron_business()
        if self.id == template_business.id:
            return None
        with transaction.atomic():
            locked_business = Business.objects.select_for_update().get(id=self.id)
            if locked_business.cloud_accounts.exists():
                return locked_business.cloud_accounts.filter(
                    is_default_production=True
                ).first()
            template_account = template_business.cloud_accounts.filter(
                is_default_production=True
            ).first()
            if not template_account:
                return None
            template_metadata = (
                template_account.metadata if isinstance(template_account.metadata, dict) else {}
            )
            return CloudAccount.objects.create(
                business=locked_business,
                name=f"{locked_business.name}-production" if locked_business.name else "production",
                provider=template_account.provider,
                account_identifier=template_account.account_identifier,
                credentials_secret_arn=template_account.credentials_secret_arn,
                metadata=copy.deepcopy(template_metadata),
                is_default_production=True,
            )


class CloudAccount(BaseErieIronModel):
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="cloud_accounts"
    )
    name = models.TextField()
    provider = models.TextField(
        choices=CloudProvider.choices(), default=CloudProvider.AWS
    )
    account_identifier = models.TextField(null=True, blank=True)
    credentials_secret_arn = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict, encoder=ErieIronJSONEncoder)
    is_default_dev = models.BooleanField(default=False)
    is_default_production = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @dataclass(slots=True)
    class AwsCredentials:
        role_arn: str
        session_name: str
        access_key_id: str
        secret_access_key: str
        session_token: Optional[str]
        expiration: datetime
    
    class Meta:
        indexes = [
            models.Index(fields=["business", "provider"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name"], name="cloudaccount_unique_business_name"
            ),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.provider})"
    
    def clear_cached_credentials(self):
        if hasattr(self, "_cached_credentials"):
            del self._cached_credentials
    
    @contextmanager
    def assume_role(self):
        """Return a sandboxed boto3 Session using this cloud account's assumed role."""
        creds = self.get_aws_credentials()
        
        # Build isolated botocore session
        import botocore.session
        botocore_sess = botocore.session.get_session()
        botocore_sess.set_credentials(
            creds.access_key_id,
            creds.secret_access_key,
            creds.session_token
        )
        
        # Wrap botocore session with boto3 Session for ergonomic APIs
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
        boto3_sess = boto3.session.Session(
            botocore_session=botocore_sess,
            region_name=region,
        )
        
        yield boto3_sess
    
    def get_aws_credentials(self) -> 'CloudAccount.AwsCredentials':
        cache_key = f"credentials_{self.id}"
        from erieiron_common import cache
        creds = cache.tl_get(cache_key)
        
        if creds and creds.expiration > common.get_now():
            return creds
        
        return cache.tl_set(cache_key, self._build_credentials())
    
    def _build_credentials(self) -> 'CloudAccount.AwsCredentials':
        if CloudProvider.AWS.neq(self.provider):
            raise NotImplementedError(f"Provider {self.provider} is not supported yet")
        
        secret_payload = self.load_credentials_secret()
        role_arn = common.get(secret_payload, "role_arn")
        if not role_arn:
            raise RuntimeError("CloudAccount AWS credential payload missing role_arn")
        
        session_name = common.get(secret_payload, "session_name") or f"erieiron-{self.id}"
        external_id = common.get(secret_payload, "external_id")
        # Session duration handling with validation and clamping
        raw_duration = common.get(secret_payload, "session_duration")
        try:
            duration_seconds = int(raw_duration)
        except Exception:
            duration_seconds = 3600  # default to 1h if missing or invalid
        
        # Clamp to AWS STS limits: 900s (15m) to 43200s (12h)
        if duration_seconds < 900:
            duration_seconds = 900
        elif duration_seconds > 43200:
            duration_seconds = 43200
        
        sts_client = boto3.client("sts")
        assume_kwargs = {
            "RoleArn": role_arn,
            "RoleSessionName": session_name,
            "DurationSeconds": duration_seconds,
        }
        if external_id:
            assume_kwargs["ExternalId"] = external_id
        
        d = {
            "cloud_account_id": str(self.id),
            "business_id": str(self.business_id),
            "provider": self.provider,
            "role_arn": role_arn,
        }
        logging.info(f"Assuming role for cloud account {d}", extra=d)
        response = sts_client.assume_role(**assume_kwargs)
        credentials = response.get("Credentials") or {}
        expiration = credentials.get("Expiration")
        if not expiration:
            expiration = common.get_now() + timedelta(hours=1)
        elif not timezone.is_aware(expiration):
            expiration = timezone.make_aware(expiration)
        
        return CloudAccount.AwsCredentials(
            role_arn=role_arn,
            session_name=session_name,
            access_key_id=credentials.get("AccessKeyId"),
            secret_access_key=credentials.get("SecretAccessKey"),
            session_token=credentials.get("SessionToken"),
            expiration=expiration,
        )
    
    def store_credentials_secret(self, payload: dict[str, Any]) -> str:
        """Persist provider credential payload for a cloud account.

        Returns the secret identifier that was written so callers can store it on the model.
        """
        if not isinstance(payload, dict):
            raise ValueError("Cloud account credential payload must be a dict")
        secret_name = self.build_secret_name()
        
        # Debug: Log current AWS context before storing
        try:
            current_identity = self.get_service_client('sts').get_caller_identity()
            logging.info(
                "Storing credential payload for cloud account",
                extra={
                    "cloud_account_id": str(self.id),
                    "business_id": str(self.business_id),
                    "provider": self.provider,
                    "secret_name": secret_name,
                    "aws_account": current_identity.get("Account"),
                    "aws_user_arn": current_identity.get("Arn"),
                },
            )
        except Exception as e:
            logging.warning(f"Could not get AWS identity for secret storage: {e}")
            logging.info(
                "Storing credential payload for cloud account",
                extra={
                    "cloud_account_id": str(self.id),
                    "business_id": str(self.business_id),
                    "provider": self.provider,
                    "secret_name": secret_name,
                },
            )
        
        from erieiron_common import aws_utils
        arn_or_name = aws_utils.put_secret(secret_name, payload)
        
        return arn_or_name or secret_name
    
    def load_credentials_secret(self) -> dict[str, Any]:
        secret_id = self.credentials_secret_arn or self.build_secret_name()
        try:
            from erieiron_common import aws_utils
            return aws_utils.get_secret(secret_id)
        except Exception as exc:
            logging.exception(
                "Failed to load credential secret for cloud account",
                extra={
                    "cloud_account_id": str(self.id),
                    "business_id": str(self.business_id),
                    "provider": self.provider,
                },
            )
            raise exc
    
    def get_service_client(self, service_name, endpoint_url=None) -> "boto3.session.Session.client":
        if CloudProvider.AWS.neq(self.provider):
            raise Exception(f"{self.provider} not supported")
        
        aws_credentials = self.get_aws_credentials()
        
        from erieiron_common.aws_utils import get_aws_region, REGION_LOCKED_US_EAST_1_SERVICES
        if service_name in REGION_LOCKED_US_EAST_1_SERVICES:
            region = "us-east-1"
        else:
            region = get_aws_region()
        
        return boto3.client(
            service_name,
            endpoint_url=endpoint_url,
            region_name=region,
            aws_session_token=aws_credentials.session_token,
            aws_secret_access_key=aws_credentials.secret_access_key,
            aws_access_key_id=aws_credentials.access_key_id,
        )
    
    def build_secret_name(self) -> str:
        if not self.id:
            raise ValueError("CloudAccount must be saved before building secret name")
        base = self.business.get_secrets_root_key(EnvironmentType.PRODUCTION)
        return f"{base}/cloud-accounts/{self.id}"
    
    def set_default_flags(
            self, *, dev: bool | None = None, production: bool | None = None
    ) -> None:
        updates: dict[str, bool] = {}
        if dev is not None:
            updates["is_default_dev"] = dev
        if production is not None:
            updates["is_default_production"] = production
        if not updates:
            return
        for field, value in updates.items():
            setattr(self, field, value)
        self.save(update_fields=list(updates.keys()))
        if updates.get("is_default_dev"):
            CloudAccount.objects.filter(
                business=self.business, is_default_dev=True
            ).exclude(id=self.id).update(is_default_dev=False)
        if updates.get("is_default_production"):
            CloudAccount.objects.filter(
                business=self.business, is_default_production=True
            ).exclude(id=self.id).update(is_default_production=False)
    
    def has_vpc_config(self) -> bool:
        """Check if VPC configuration exists in metadata."""
        return bool(self.metadata.get("vpc"))
    
    def get_vpc_config(self) -> dict | None:
        """Retrieve VPC configuration from metadata with validation."""
        vpc_config = self.metadata.get("vpc")
        if not vpc_config:
            return None
        
        # Validate required fields
        required_fields = ["vpc_id", "cidr_block", "public_subnets", "private_subnets"]
        for field in required_fields:
            if field not in vpc_config:
                logging.warning(
                    f"CloudAccount {self.id} VPC config missing required field: {field}"
                )
                return None
        
        # Validate subnet structure
        for subnet_type in ["public_subnets", "private_subnets"]:
            subnets = vpc_config.get(subnet_type, [])
            if not isinstance(subnets, list):
                logging.warning(
                    f"CloudAccount {self.id} VPC config {subnet_type} must be a list"
                )
                return None
            for subnet in subnets:
                if not isinstance(subnet, dict):
                    logging.warning(
                        f"CloudAccount {self.id} VPC config {subnet_type} items must be dicts"
                    )
                    return None
                if "name" not in subnet and "cidr_block" not in subnet:
                    logging.warning(
                        f"CloudAccount {self.id} VPC subnet entries must include a name or cidr_block"
                    )
                    return None
        
        return vpc_config
    
    def set_vpc_config(self, vpc_data: dict) -> None:
        """Store VPC configuration in metadata with schema validation."""
        if not isinstance(vpc_data, dict):
            raise ValueError("VPC data must be a dictionary")
        
        # Validate schema structure
        required_fields = ["vpc_id", "cidr_block", "public_subnets", "private_subnets"]
        for field in required_fields:
            if field not in vpc_data:
                raise ValueError(f"VPC config missing required field: {field}")
        
        # Validate subnet structure
        for subnet_type in ["public_subnets", "private_subnets"]:
            subnets = vpc_data.get(subnet_type, [])
            if not isinstance(subnets, list):
                raise ValueError(f"VPC config {subnet_type} must be a list")
            for subnet in subnets:
                if not isinstance(subnet, dict):
                    raise ValueError(f"VPC config {subnet_type} items must be dicts")
                if "name" not in subnet and "cidr_block" not in subnet:
                    raise ValueError(
                        "VPC subnet entries must include at least a name or cidr_block"
                    )
        
        # Store in metadata
        if "vpc" not in self.metadata:
            self.metadata["vpc"] = {}
        self.metadata["vpc"] = vpc_data
        self.save(update_fields=["metadata"])


class BusinessHumanJobDescription(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    estimated_hours_per_week = models.IntegerField()
    job_description = models.TextField()


class BusinessSecondOpinionEvaluation(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    llm_model = models.TextField(choices=LlmModel.choices())
    evaluation = models.JSONField(default=dict, encoder=ErieIronJSONEncoder)
    timestamp = models.DateTimeField(auto_now_add=True)


class BusinessAnalysis(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    business_name = models.TextField()
    estimated_monthly_revenue_at_steady_state_usd = models.FloatField(null=True)
    time_to_profit_estimate_months = models.IntegerField(null=True)
    potential_mode = models.TextField(null=True)
    summary = models.TextField(null=True)
    
    final_recommendation_justification = models.TextField(null=True)
    final_recommendation_score_1_to_10 = models.IntegerField(null=True)
    
    estimated_operating_total_cost_per_month_usd = models.FloatField(null=True)
    
    upfront_investment_estimated_amount_usd = models.FloatField(null=True)
    
    total_addressable_market_estimate_usd_per_year = models.FloatField(null=True)
    total_addressable_market_source_or_rationale = models.TextField(null=True)
    
    macro_trends_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    risks_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    monthly_expenses_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    potential_competitors_data = models.JSONField(
        null=True, encoder=ErieIronJSONEncoder
    )
    use_of_funds_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)


class BusinessGuidance(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    guidance = models.TextField(choices=BusinessGuidanceRating.choices())
    justification = models.TextField(null=True)


class BusinessLegalAnalysis(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    
    approved = models.BooleanField()
    justification = models.TextField(null=True)
    required_disclaimers_or_terms = models.JSONField(
        null=True, encoder=ErieIronJSONEncoder
    )
    risk_rating = models.TextField(null=True, choices=Level.choices())
    recommended_entity_structure = models.TextField(
        choices=LlcStructure.choices(), null=False
    )
    entity_structure_justification = models.TextField(null=True)


class BusinessBankBalanceSnapshot(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    
    def get_aggregate_balance_data(self, reserve_percent=0):
        available_balance = []
        current_balance = []
        status = []
        for account in self.businessbankbalancesnapshotaccount_set.all():
            available_balance.append(account.available_balance)
            current_balance.append(account.current_balance)
            status.append(account.status)
        
        return {
            "available_balance": common.safe_sum(available_balance)
                                 * (1 - reserve_percent),
            "current_balance": common.safe_sum(current_balance) * (1 - reserve_percent),
            "status": common.join_with_and(list(set(status))),
        }


class BusinessBankBalanceSnapshotAccount(BaseErieIronModel):
    snapshot = models.ForeignKey(
        "BusinessBankBalanceSnapshot", on_delete=models.CASCADE
    )
    account_name = models.TextField()
    account_id = models.TextField()
    available_balance = models.FloatField(null=True)
    current_balance = models.FloatField(null=True)
    status = models.TextField(null=True)


class BusinessCapacityAnalysis(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    
    cash_capacity_status = models.TextField(choices=TrafficLight.choices())
    compute_capacity_status = models.TextField(choices=TrafficLight.choices())
    human_capacity_status = models.TextField(choices=TrafficLight.choices())
    recommendation = models.TextField(choices=TrafficLight.choices())
    
    justification = models.TextField(null=True)


class BusinessKPI(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    kpi_id = models.TextField()
    name = models.TextField()
    description = models.TextField(null=True)
    target_value = models.FloatField()
    unit = models.TextField()
    priority = models.TextField(choices=Level.choices())


class BusinessKPIProgress(BaseErieIronModel):
    kpi = models.ForeignKey(BusinessKPI, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    value = models.FloatField()


class BusinessGoal(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    goal_id = models.TextField()
    kpi = models.ForeignKey(BusinessKPI, on_delete=models.CASCADE)
    description = models.TextField()
    target_value = models.FloatField()
    unit = models.TextField()
    due_date = models.DateField()
    priority = models.TextField(choices=Level.choices())
    status = models.TextField(choices=GoalStatus.choices())


class BusinessCeoDirective(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    target_agent = models.TextField()
    directive_summary = models.TextField()
    goal_alignment = models.JSONField(default=list)
    kpi_targets = models.JSONField(default=dict)


class Initiative(BaseErieIronModel):
    id = models.TextField(primary_key=True)  # initiative_token
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    architecture = models.TextField(null=True)
    user_documentation = models.TextField(null=True)
    initiative_type = models.TextField(
        choices=InitiativeType.choices(), default=InitiativeType.PRODUCT
    )
    created_timestamp = models.DateTimeField(auto_now_add=True)
    title = models.TextField()
    description = models.TextField()
    details = models.TextField(null=True)
    priority = models.TextField(choices=Level.choices())
    linked_kpis = models.ManyToManyField(
        "BusinessKPI", related_name="initiatives", blank=True
    )
    linked_goals = models.ManyToManyField(
        "BusinessGoal", related_name="initiatives", blank=True
    )
    expected_kpi_lift = models.JSONField(default=dict)
    requires_unit_tests = models.BooleanField(default=True)
    domain = models.TextField(null=True)
    green_lit = models.BooleanField(default=False)
    
    def write_user_documentation(self):
        from erieiron_autonomous_agent.system_agent_llm_interface import (
            llm_chat,
            get_sys_prompt,
        )
        from erieiron_autonomous_agent.coding_agents.coding_agent import (
            get_existing_test_context_messages,
        )
        
        self.user_documentation = llm_chat(
            "Write User Documentation",
            [
                get_sys_prompt("initiative--user_documentation_writer.md"),
                textwrap.dedent(
                    f"""
                    ## Feature Description
                    {self.description}
                """
                ),
                textwrap.dedent(
                    f"""
                    ## Architecture
                    {self.architecture}
                """
                ),
                get_existing_test_context_messages(self, title="Automated Tests"),
                textwrap.dedent(
                    f"""
                ## Domain Name to use in docs:
                {self.business.domain}
            """
                ),
            ],
            verbosity=LlmVerbosity.HIGH,
            tag_entity=self,
        ).text
        self.save()
    
    def all_tasks_complete(self) -> bool:
        if self.tasks.count() == 0:
            return False
        
        return not self.tasks.exclude(status=TaskStatus.COMPLETE).exists()
    
    def llm_data(self):
        return {
            "initiative_id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "requirements": [
                {
                    "summary": req.summary,
                    "acceptance_criteria": req.acceptance_criteria,
                    "testable": req.testable,
                }
                for req in self.requirements.order_by()
            ],
        }
    
    def get_first_task_to_implement(self) -> "Task":
        return (
            self.tasks.exclude(status__in=[TaskStatus.BLOCKED, TaskStatus.COMPLETE])
            .order_by("created_timestamp")
            .first()
        )


class WorkflowDefinition(BaseErieIronModel):
    name = models.TextField(unique=True)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    updated_timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def with_graph(cls) -> QuerySet["WorkflowDefinition"]:
        return cls.objects.prefetch_related(
            models.Prefetch(
                "steps",
                queryset=WorkflowStep.objects.order_by("sort_order", "name"),
            ),
            models.Prefetch(
                "triggers",
                queryset=WorkflowTrigger.objects.select_related("target_step").order_by(
                    "sort_order",
                    "message_type",
                ),
            ),
            models.Prefetch(
                "connections",
                queryset=WorkflowConnection.objects.select_related(
                    "source_step",
                    "target_step",
                ).order_by("sort_order", "id"),
            ),
        )

    @classmethod
    def active_workflows(cls) -> QuerySet["WorkflowDefinition"]:
        return cls.with_graph().filter(is_active=True)

    @classmethod
    def register_active_workflows(cls, pubsub_manager):
        for workflow in cls.active_workflows():
            workflow.register(pubsub_manager)

    def register(self, pubsub_manager):
        step_triggers: dict[uuid.UUID, list[WorkflowTrigger]] = {}
        step_connections: dict[uuid.UUID, list[WorkflowConnection]] = {}

        for trigger in self.triggers.all():
            step_triggers.setdefault(trigger.target_step_id, []).append(trigger)

        for connection in self.connections.all():
            step_connections.setdefault(connection.target_step_id, []).append(connection)

        for step in self.steps.all():
            completed_message_type = step.get_completed_message_type()
            handler = step.get_handler()

            for trigger in step_triggers.get(step.id, []):
                pubsub_manager.on(
                    PubSubMessageType(trigger.message_type),
                    handler,
                    completed_message_type,
                )

            for connection in step_connections.get(step.id, []):
                pubsub_manager.on(
                    PubSubMessageType(connection.message_type),
                    handler,
                    completed_message_type,
                )


class WorkflowStep(BaseErieIronModel):
    workflow = models.ForeignKey(
        WorkflowDefinition,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    name = models.TextField()
    handler_path = models.TextField(
        help_text="Dotted handler path such as package.module.function."
    )
    emits_message_type = models.TextField(
        choices=PubSubMessageType.choices(),
        null=True,
        blank=True,
        help_text="Message type published after this step completes.",
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=("workflow", "name"),
                name="uniq_workflow_step_name",
            ),
        ]
        indexes = [
            models.Index(fields=["workflow", "sort_order"]),
            models.Index(fields=["workflow", "emits_message_type"]),
        ]

    def __str__(self):
        return f"{self.workflow.name}: {self.name}"

    def get_handler(self):
        return common.deserialize_symbol(self.handler_path)

    def get_completed_message_type(self) -> PubSubMessageType | None:
        if not self.emits_message_type:
            return None

        return PubSubMessageType(self.emits_message_type)


class WorkflowTrigger(BaseErieIronModel):
    workflow = models.ForeignKey(
        WorkflowDefinition,
        on_delete=models.CASCADE,
        related_name="triggers",
    )
    target_step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.CASCADE,
        related_name="triggers",
    )
    message_type = models.TextField(choices=PubSubMessageType.choices())
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "message_type"]
        constraints = [
            models.UniqueConstraint(
                fields=("workflow", "target_step", "message_type"),
                name="uniq_workflow_trigger",
            ),
        ]
        indexes = [
            models.Index(fields=["workflow", "sort_order"]),
            models.Index(fields=["workflow", "message_type"]),
        ]

    def clean(self):
        super().clean()
        if self.target_step_id and self.workflow_id != self.target_step.workflow_id:
            raise ValidationError(
                {"target_step": "Workflow triggers must point to a step in the same workflow."}
            )

    def __str__(self):
        return f"{self.workflow.name}: {self.message_type} -> {self.target_step.name}"


class WorkflowConnection(BaseErieIronModel):
    workflow = models.ForeignKey(
        WorkflowDefinition,
        on_delete=models.CASCADE,
        related_name="connections",
    )
    source_step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.CASCADE,
        related_name="outgoing_connections",
    )
    target_step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.CASCADE,
        related_name="incoming_connections",
    )
    message_type = models.TextField(choices=PubSubMessageType.choices())
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("workflow", "source_step", "target_step", "message_type"),
                name="uniq_workflow_connection",
            ),
        ]
        indexes = [
            models.Index(fields=["workflow", "sort_order"]),
            models.Index(fields=["workflow", "message_type"]),
            models.Index(fields=["source_step", "target_step"]),
        ]

    def clean(self):
        super().clean()
        if self.source_step_id and self.workflow_id != self.source_step.workflow_id:
            raise ValidationError(
                {"source_step": "Workflow connections must start from a step in the same workflow."}
            )
        if self.target_step_id and self.workflow_id != self.target_step.workflow_id:
            raise ValidationError(
                {"target_step": "Workflow connections must point to a step in the same workflow."}
            )
        if (
            self.source_step_id
            and self.source_step.emits_message_type
            and self.message_type != self.source_step.emits_message_type
        ):
            raise ValidationError(
                {"message_type": "Workflow connection message type must match the source step output."}
            )

    def __str__(self):
        return (
            f"{self.workflow.name}: {self.source_step.name} -[{self.message_type}]-> "
            f"{self.target_step.name}"
        )


class InfrastructureStack(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    initiative = models.ForeignKey(
        Initiative,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cloudformation_stacks",
    )
    cloud_account = models.ForeignKey(
        "CloudAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stacks",
    )
    stack_namespace_token = models.TextField(unique=True)
    stack_name = models.TextField(unique=True)
    stack_arn = models.TextField(null=True)
    env_type = models.TextField(choices=EnvironmentType.choices())
    stack_configuration = models.TextField(null=True)
    stack_type = models.TextField(choices=InfrastructureStackType.choices())
    imported_shared_resources = models.JSONField(default=dict, null=True, encoder=ErieIronJSONEncoder)
    stack_vars = models.JSONField(default=dict, null=True, encoder=ErieIronJSONEncoder)
    credential_arns = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    resources = models.JSONField(default=dict, null=True, encoder=ErieIronJSONEncoder)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    updated_timestamp = models.DateTimeField(auto_now_add=True)
    
    def get_credential_arns(self, required_provision: CredentialServiceProvisioning = None) -> list[tuple[CredentialService, str]]:
        out = [
            (credential_service, self.get_credential_arn(credential_service))
            for credential_service in self.business.get_required_credentials()
        ]
        
        if required_provision:
            out = [
                t for t in out if required_provision.eq(t[0].get_provisioning())
            ]
        
        return out
    
    def get_runtime_env(self) -> dict:
        cloud_credentials = self.get_cloud_credentials()
        
        env = {
            **cloud_credentials,
            "DOMAIN_NAME": self.business.domain if EnvironmentType.PRODUCTION.eq(self.env_type) else self.initiative.domain,
            "STACK_NAME": self.stack_name,
            "STACK_IDENTIFIER": self.stack_namespace_token,
            "TASK_NAMESPACE": self.stack_namespace_token,
            "BUILDAH_FORMAT": "docker",
            "PATH": os.getenv("PATH")
        }
        
        hf_model_cache_s3_uri = settings.HF_MODEL_CACHE_S3_URI
        if hf_model_cache_s3_uri:
            env["HF_MODEL_CACHE_S3_URI"] = hf_model_cache_s3_uri
        
        for credential_service in self.business.get_required_credentials():
            _, env_var = credential_service.get_stackoutput_and_env_names()
            arn = self.get_credential_arn(credential_service)
            if arn:
                env[env_var] = arn
            else:
                logging.error(f"Missing credential ARN for {credential_service}")
        
        for k in list(env.keys()):
            if k == "AWS_PROFILE" or k.startswith("__") or env.get(k) is None:
                env.pop(k, None)
        
        return env
    
    def get_credential_arn(self, credential_service: CredentialService) -> str:
        """Cascading lookup: stack → business → erie iron → error

        Args:
            self: InfrastructureStack instance requiring credentials
            credential_service: CredentialService enum member

        Returns:
            Secret ARN string

        Raises:
            ValueError: If no credential ARN found at any level
        """
        # 1. Check stack-level override
        credential_service = CredentialService(credential_service)
        
        arn = common.get(self.credential_arns, credential_service.value)
        if arn:
            return arn
        
        arn = common.get(self.business.credential_arns, credential_service.value)
        if arn:
            return arn
        
        arn = common.get(Business.get_erie_iron_business().credential_arns, credential_service.value)
        if arn:
            return arn
        
        return None
    
    def get_cloud_account(self) -> CloudAccount:
        return (
                self.cloud_account
                or self.business.get_default_cloud_account(self.env_type)
                or Business.get_erie_iron_business().get_default_cloud_account(self.env_type)
        )
    
    def get_cloud_credentials(self) -> dict[str, str]:
        business = self.business
        env_type = EnvironmentType(self.env_type)
        
        cloud_account = self.get_cloud_account()
        cloud_account_credentials = cloud_account.get_aws_credentials()
        
        aws_region = env_type.get_aws_region()
        env = {
            "CLOUD_ACCOUNT_IDENTIFIER": cloud_account.account_identifier,
            "ROLE_ARN": cloud_account_credentials.role_arn,
            "ROLE_SESSION_NAME": cloud_account_credentials.session_name,
            "AWS_ACCESS_KEY_ID": cloud_account_credentials.access_key_id,
            "AWS_SECRET_ACCESS_KEY": cloud_account_credentials.secret_access_key,
            "AWS_DEFAULT_REGION": aws_region,
            "AWS_REGION": aws_region
        }
        
        if cloud_account_credentials.session_token:
            env["AWS_SESSION_TOKEN"] = cloud_account_credentials.session_token
        
        return env
    
    def get_iac_state_metadata(self) -> dict[str, Any]:
        raw_value = self.stack_arn
        if not raw_value:
            return {}
        
        if isinstance(raw_value, dict):
            return raw_value
        
        if isinstance(raw_value, str):
            trimmed = raw_value.strip()
            if trimmed.startswith("{"):
                try:
                    parsed = json.loads(trimmed)
                except json.JSONDecodeError:
                    return {"provider": "unknown", "state_locator": raw_value}
                if isinstance(parsed, dict):
                    return parsed
                return {"provider": "unknown", "state_locator": raw_value}
            if trimmed.startswith("arn:"):
                return {"provider": "cloudformation", "state_locator": raw_value}
            if trimmed.startswith("opentofu://"):
                return {"provider": "opentofu", "state_locator": raw_value}
            return {"provider": "unknown", "state_locator": raw_value}
        
        return {"provider": "unknown", "state_locator": str(raw_value)}
    
    @property
    def iac_state_locator(self) -> str | None:
        metadata = self.get_iac_state_metadata()
        return (
                metadata.get("state_locator")
                or metadata.get("state_file")
                or metadata.get("workspace_dir")
                or metadata.get("workspace_name")
                or self.stack_arn
        )
    
    @property
    def iac_provider(self) -> str:
        metadata = self.get_iac_state_metadata()
        provider = metadata.get("provider")
        if provider:
            return str(provider).lower()
        if self.stack_arn and str(self.stack_arn).startswith("arn:"):
            return "cloudformation"
        return getattr(settings, "SELF_DRIVING_IAC_PROVIDER", "opentofu").lower()
    
    @staticmethod
    def get_stack(
            initiative: Initiative,
            stack_type: InfrastructureStackType,
            env_type: EnvironmentType,
            assert_create=False,
    ) -> "InfrastructureStack":
        """
        Get or create infrastructure stack based on business stack_strategy.

        This method is being phased out in favor of get_stack_for_task().
        For new code, prefer using resolve_target_stack() + get_stack_for_task().
        """
        from erieiron_common.aws_utils import sanitize_aws_name
        
        business = initiative.business
        
        # Determine initiative scope based on env_type and strategy
        # This maintains backward compatibility for existing callers
        if EnvironmentType.PRODUCTION.eq(env_type):
            initiative_scope = None  # Production is always business-level
        else:
            strategy = StackStrategy(common.default_str(business.stack_strategy).lower())
            if strategy.allows_initiative_stacks():
                initiative_scope = initiative
            else:
                # SINGLE_STACK and PROD_AND_DEV use business-level stacks
                initiative_scope = None
        
        # Try to find existing stack
        stack = InfrastructureStack.objects.filter(
            business_id=business.id,
            initiative=initiative_scope,
            stack_type=stack_type,
            env_type=env_type,
        ).first()
        
        if stack:
            if assert_create:
                raise Exception("was supposed to create new but did not")
            return stack
        
        stack_namespace_token = None
        for i in range(100):
            stack_namespace_token_candidate = common.gen_random_token(DEV_STACK_TOKEN_LENGTH)
            if not InfrastructureStack.objects.filter(
                    stack_namespace_token=stack_namespace_token_candidate
            ).exists():
                stack_namespace_token = stack_namespace_token_candidate
                break
        
        if not stack_namespace_token:
            raise Exception(f"unable to find a unique stack_namespace_token")
        
        # Generate stack name
        if EnvironmentType.PRODUCTION.eq(env_type):
            stack_name = sanitize_aws_name(
                [stack_namespace_token, business.service_token, stack_type]
            )
        else:
            if initiative_scope:
                stack_name = sanitize_aws_name(
                    [stack_namespace_token, initiative.id, stack_type]
                )
            else:
                # Business-level dev stack
                stack_name = sanitize_aws_name(
                    [stack_namespace_token, business.service_token, "dev", stack_type]
                )
        
        domain_manager = business.get_domain_manager()
        cloud_account = domain_manager.cloud_account
        
        stack = InfrastructureStack.objects.create(
            business=business,
            initiative=initiative_scope,
            cloud_account=cloud_account,
            stack_type=stack_type,
            stack_name=stack_name,
            stack_namespace_token=stack_namespace_token,
            env_type=env_type,
        )
        
        # Set up domain for dev stacks
        if InfrastructureStackType.APPLICATION.eq(stack_type) and EnvironmentType.DEV.eq(env_type):
            new_sub_domain = f"{sanitize_aws_name(stack_name, 63)}.{business.domain}"
            
            if initiative_scope:
                # Initiative-scoped: update initiative domain
                Initiative.objects.filter(id=initiative.id).update(domain=new_sub_domain)
                initiative.refresh_from_db(fields=["domain"])
            # For business-level dev stack, we don't update initiative domain
            
            zone_id = business.route53_hosted_zone_id
            if not zone_id:
                from erieiron_common import aws_utils
                zone_id = domain_manager.find_hosted_zone_id(business.domain)
            
            domain_manager.add_dns_records(zone_id, new_sub_domain)
        
        return stack
    
    @staticmethod
    def get_stack_for_task(
            task: 'Task',
            stack_type: InfrastructureStackType,
    ) -> "InfrastructureStack":
        """
        Get or create the appropriate stack for a task based on business stack_strategy.

        This is the preferred method for task execution code.
        It uses resolve_target_stack() to determine the correct stack.
        """
        business = task.initiative.business
        env_type = task.resolve_target_env_type(stack_type)
        
        # Use task's initiative as context, but actual scope determined by resolver
        return InfrastructureStack.get_stack(
            task.initiative,
            stack_type,
            env_type,
        )
    
    def delete_resources(self, force=False):
        if not force:
            if EnvironmentType.PRODUCTION.eq(self.env_type):
                raise Exception(f"cannot destroy a production stack.  needs to be done manually")
        
        if not self.resources:
            return self
        
        try:
            from erieiron_common.stack_manager import StackManager
            StackManager(
                self,
                container_env=self.get_runtime_env()
            ).init_workspace().destroy_stack()
        except Exception as e:
            logging.warning(f"Unable to delete stack {self.stack_name}:  {e}")
        
        return self
    
    def get_template_name(self) -> str:
        return InfrastructureStackType(self.stack_type).get_opentofu_config()
    
    class Meta:
        indexes = [
            models.Index(fields=["business", "stack_type"]),
            models.Index(fields=["initiative", "stack_type"]),
            models.Index(fields=["cloud_account", "stack_type"]),
        ]
    
    def __str__(self):
        initiative_id = self.initiative_id or "no-initiative"
        return f"{self.stack_type}::{initiative_id}::{self.stack_name}"


class ProductRequirement(BaseErieIronModel):
    id = models.TextField(primary_key=True)  # requirement_token
    initiative = models.ForeignKey(
        Initiative, on_delete=models.CASCADE, related_name="requirements"
    )
    product_initiative = models.TextField(null=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    summary = models.TextField()
    acceptance_criteria = models.TextField()
    testable = models.BooleanField(default=True)


class Task(BaseErieIronModel):
    id = models.TextField(primary_key=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    initiative = models.ForeignKey(
        Initiative, on_delete=models.CASCADE, related_name="tasks"
    )
    product_initiative = models.TextField(null=True)
    task_type = models.TextField(
        choices=TaskType.choices(), default=TaskType.CODING_APPLICATION, null=False
    )
    status = models.TextField(null=False, choices=TaskStatus.choices())
    debug_steps = models.TextField(null=True)
    
    validated_requirements = models.ManyToManyField(
        ProductRequirement, blank=True, related_name="validation_tasks"
    )
    description = models.TextField()
    depends_on = models.ManyToManyField(
        "self", symmetrical=False, related_name="dependent_tasks", blank=True
    )
    risk_notes = models.TextField()
    completion_criteria = models.JSONField(default=list)
    comment_requests = models.JSONField(default=list)
    current_spend = models.FloatField(null=True)
    max_budget_usd = models.FloatField(null=True)
    attachments = models.JSONField(default=list)
    created_by = models.TextField(null=True)
    
    input_fields = models.JSONField(default=dict)
    output_fields = models.JSONField(default=list)
    
    requires_test = models.BooleanField(default=True)
    execution_schedule = models.TextField(
        choices=TaskExecutionSchedule.choices(), default=TaskExecutionSchedule.ONCE
    )
    execution_start_time = models.DateTimeField(null=True, blank=True)
    timeout_seconds = models.IntegerField(null=True, blank=True)
    guidance = models.TextField(null=True, blank=True)
    implementation_source_kind = models.TextField(
        choices=TaskImplementationSourceKind.choices(),
        null=True,
        blank=True,
    )
    active_implementation_version = models.ForeignKey(
        "TaskImplementationVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    
    implementation_phase = models.CharField(
        max_length=50,
        choices=TaskImplementationPhase.choices(),
        null=True,
        blank=True,
        help_text="For React/React Native projects: tracks whether this task is UI-first phase or server phase"
    )
    
    def __str__(self):
        return f"{self.description} - {self.id}"
    
    def to_dict(self):
        d = self.__dict__
        
        return d
    
    def resolve_target_env_type(
            self,
            stack_type: InfrastructureStackType
    ) -> EnvironmentType:
        """
        Central resolver for determining which stack a task should use.

        Returns: (env_type, initiative_or_none)
            - env_type: PRODUCTION or DEV
            - initiative_or_none: Initiative to scope stack to, or None for business-level

        Raises:
            ValueError: If task configuration is incompatible with business stack_strategy
        """
        initiative = self.initiative
        strategy = StackStrategy(common.default_str( initiative.business.stack_strategy).lower())
        is_prod_deploy = TaskType.PRODUCTION_DEPLOYMENT.eq(self.task_type)
        
        if strategy.requires_production_only() or is_prod_deploy:
            return EnvironmentType.PRODUCTION
        else:
            return EnvironmentType.DEV
    
    def get_implementation_versions(self) -> QuerySet["TaskImplementationVersion"]:
        return self.taskimplementationversion_set.order_by("-version_number", "-created_at")
    
    def get_active_implementation_version(self) -> Optional["TaskImplementationVersion"]:
        if self.active_implementation_version_id:
            return self.active_implementation_version
        return self.taskimplementationversion_set.order_by("version_number", "created_at").last()
    
    def get_implementation_source_kind_enum(self) -> Optional[TaskImplementationSourceKind]:
        if self.implementation_source_kind:
            return TaskImplementationSourceKind(self.implementation_source_kind)
        
        active_version = self.get_active_implementation_version()
        if active_version:
            return active_version.source_kind_enum()
        
        return None
    
    def create_implementation_version(
            self,
            *,
            source_kind: str | TaskImplementationSourceKind | None = None,
            application_repo_file_path: str | None = None,
            application_repo_ref: str | None = None,
            source_metadata: dict | None = None,
            evaluator_config: dict | None = None,
            set_active: bool = True,
    ) -> "TaskImplementationVersion":
        source_kind = source_kind or self.implementation_source_kind
        if not source_kind:
            raise ValidationError("task implementation source kind is required")
        
        source_kind = TaskImplementationSourceKind(source_kind)
        existing_kind = self.get_implementation_source_kind_enum()
        if existing_kind and existing_kind.neq(source_kind):
            raise ValidationError("task implementation source kind cannot change once versions exist")
        
        next_version_number = (
            self.taskimplementationversion_set.aggregate(max_version=models.Max("version_number"))["max_version"]
            or 0
        ) + 1
        
        version = TaskImplementationVersion.objects.create(
            task=self,
            source_kind=source_kind.value,
            version_number=next_version_number,
            application_repo_file_path=application_repo_file_path,
            application_repo_ref=application_repo_ref,
            source_metadata=copy.deepcopy(source_metadata or {}),
            evaluator_config=copy.deepcopy(evaluator_config or {}),
        )
        
        update_fields = {}
        if self.implementation_source_kind != source_kind.value:
            update_fields["implementation_source_kind"] = source_kind.value
        if set_active:
            update_fields["active_implementation_version"] = version
        
        if update_fields:
            Task.objects.filter(id=self.id).update(**update_fields)
            self.refresh_from_db(fields=list(update_fields.keys()))
        
        return version
    
    def build_execution_model_metadata(
            self,
            *,
            iteration: Optional["SelfDrivingTaskIteration"] = None,
            model_metadata: dict | None = None,
    ) -> dict[str, Any]:
        if model_metadata is not None:
            return copy.deepcopy(model_metadata)
        
        if not iteration:
            return {}
        
        metadata: dict[str, Any] = {}
        if iteration.planning_model:
            metadata["planning_model"] = iteration.planning_model
        if iteration.coding_model:
            metadata["coding_model"] = iteration.coding_model
        
        llm_models = list(
            iteration.llmrequest_set.exclude(llm_model__isnull=True)
            .exclude(llm_model="")
            .values_list("llm_model", flat=True)
            .distinct()
        )
        if llm_models:
            metadata["llm_models"] = llm_models
        
        return metadata
    
    def evaluate_execution(
            self,
            *,
            implementation_version: Optional["TaskImplementationVersion"] = None,
            crashed: bool = False,
            score: float | None = None,
            metadata: dict | None = None,
    ) -> TaskExecutionEvaluationResult:
        implementation_version = implementation_version or self.get_active_implementation_version()
        evaluator_spec = implementation_version.get_evaluator_spec() if implementation_version else {
            "kind": "default",
            "config": {},
        }
        
        if score is None:
            final_score = 0.0 if crashed else 1.0
        else:
            final_score = max(0.0, min(1.0, float(score)))
        
        evaluation_metadata = copy.deepcopy(metadata or {})
        evaluation_metadata["evaluator"] = evaluator_spec
        
        return TaskExecutionEvaluationResult(
            score=final_score,
            metadata=evaluation_metadata,
        )
    
    def create_execution(
            self,
            input_data=None,
            iteration=None,
            model_metadata: dict | None = None,
    ) -> "TaskExecution":
        implementation_version = self.get_active_implementation_version()
        implementation_source_kind = (
            implementation_version.source_kind
            if implementation_version
            else common.default_str(self.implementation_source_kind) or None
        )
        implementation_provenance = (
            implementation_version.build_execution_provenance(self.initiative.business)
            if implementation_version
            else {}
        )
        
        with transaction.atomic():
            return TaskExecution.objects.create(
                task=self,
                iteration=iteration,
                status=TaskStatus.NOT_STARTED,
                input=input_data or {},
                implementation_version=implementation_version,
                implementation_source_kind=implementation_source_kind,
                implementation_provenance=implementation_provenance,
                model_metadata=self.build_execution_model_metadata(
                    iteration=iteration,
                    model_metadata=model_metadata,
                ),
            )
    
    def get_last_execution(self) -> Optional["TaskExecution"]:
        return (
            self.taskexecution_set.filter(executed_time__isnull=False)
            .order_by("executed_time")
            .last()
        )
    
    def are_dependencies_complete(self):
        return all(dep.status == TaskStatus.COMPLETE for dep in self.depends_on.all())
    
    def get_work_desc(self):
        completion_criteria = "\n".join(common.ensure_list(self.completion_criteria))
        
        return f"""
## GOAL
{self.description}

## Completion Criteria
{self.completion_criteria}
        """
    
    def update_dependent_tasks(self):
        from erieiron_common.message_queue.pubsub_manager import PubSubManager
        from erieiron_common.enums import PubSubMessageType
        
        for t in self.depends_on.filter(
                status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]
        ):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)
        for t in self.dependent_tasks.filter(
                status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]
        ):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)
    
    def is_ui_first_phase(self):
        """Returns True if this task is in the UI + Mock API phase (no AWS deployment)"""
        return TaskImplementationPhase.UI_MOCK_API.eq(self.implementation_phase)
    
    def is_server_phase(self):
        """Returns True if this task is in the Server API phase (with AWS deployment)"""
        return TaskImplementationPhase.SERVER_IMPLEMENTATION.eq(self.implementation_phase)
    
    def allow_execution(self):
        b = self.initiative.business
        if Business.get_erie_iron_business().id == b.id:
            return True
        
        # Check if initiative is green lit
        if not self.initiative.green_lit:
            return False
        
        return BusinessStatus.ACTIVE.eq(self.initiative.business.status)
    
    def create_self_driving_env(self, reset_code_dir=False) -> "SelfDrivingTask":
        business = self.initiative.business
        
        self_driving_task, created = SelfDrivingTask.objects.get_or_create(
            task_id=self.id,
            defaults={
                "sandbox_path": os.path.abspath(tempfile.TemporaryDirectory().name),
                "main_name": common.safe_filename(self.id),
                "goal": self.get_work_desc(),
                "business": business,
            },
        )
        
        if not Path(self_driving_task.sandbox_path).exists():
            reset_code_dir = True
        
        if reset_code_dir and not created:
            if Path(self_driving_task.sandbox_path).exists():
                common.delete_dir(self_driving_task.sandbox_path)
            
            SelfDrivingTask.objects.filter(pk=self_driving_task.pk).update(
                sandbox_path=os.path.abspath(tempfile.TemporaryDirectory().name)
            )
            self_driving_task.refresh_from_db(fields=["sandbox_path"])
        
        from erieiron_autonomous_agent.business_level_agents.eng_lead import (
            bootstrap_repo,
        )
        
        git = self_driving_task.get_git()
        repo_url = business.get_application_repo_url()
        try:
            if git.source_exists():
                git.pull()
            else:
                git.clone(repo_url)
        except Exception as e:
            if "repository not found" in str(e).lower():
                bootstrap_repo(business, git)
                if git.source_exists():
                    git.pull()
                else:
                    git.clone(repo_url)
            else:
                raise e
        
        return self_driving_task
    
    def get_upstream_outputs(self):
        return {
            task.id: task.get_last_execution().output for task in self.depends_on.all()
        }
    
    def get_sub_domain(self) -> str:
        from erieiron_common.aws_utils import sanitize_aws_name
        
        return sanitize_aws_name([str(self.id)], max_length=63).lower()
    
    def get_name(self):
        root_str = str(self.id)
        
        if "task_" in root_str:
            root_str = root_str[len("task_"):]
        
        root_str = root_str.split("--")[-1]
        
        return root_str.replace("_", " ").replace("-", " ").capitalize()
    
    def restart(self):
        try:
            common.delete_dir(self.selfdrivingtask.sandbox_path)
        except:
            ...
        
        SelfDrivingTaskIteration.objects.filter(self_driving_task__task_id=self.id).delete()
        CodeFile.objects.filter(business_id=self.initiative.business_id).delete()
        
        # Reset task status and clear any existing executions
        Task.objects.filter(id=self.id).update(
            status=TaskStatus.NOT_STARTED
        )
        
        if SelfDrivingTask.objects.filter(task_id=self.id).exists():
            SelfDrivingTask.objects.filter(id=self.selfdrivingtask.id).update(
                test_file_path=None,
                initial_tests_pass=False
            )
    
    def get_container_image_tag(self) -> str:
        last_iteration_with_container = SelfDrivingTaskIteration.objects.filter(
            self_driving_task__id=self.selfdrivingtask.id,
            docker_tag__isnull=False
        ).order_by(
            "timestamp"
        ).last()
        
        return last_iteration_with_container.docker_tag if last_iteration_with_container else None


class TaskExecution(BaseErieIronModel):
    implementation_version = models.ForeignKey(
        "TaskImplementationVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    implementation_source_kind = models.TextField(
        choices=TaskImplementationSourceKind.choices(),
        null=True,
        blank=True,
    )
    implementation_provenance = models.JSONField(default=dict, encoder=ErieIronJSONEncoder)
    model_metadata = models.JSONField(default=dict, encoder=ErieIronJSONEncoder)
    evaluation_score = models.FloatField(null=True)
    evaluation_metadata = models.JSONField(default=dict, encoder=ErieIronJSONEncoder)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    iteration = models.ForeignKey(
        "SelfDrivingTaskIteration", on_delete=models.CASCADE, null=True
    )
    status = models.TextField(
        default=TaskStatus.NOT_STARTED, null=False, choices=TaskStatus.choices()
    )
    created_time = models.DateTimeField(auto_now_add=True)
    executed_time = models.DateTimeField(null=True)
    input = models.JSONField(default=dict, null=True)
    output = models.JSONField(default=dict, null=True)
    error_msg = models.TextField(null=True)
    
    def resolve(
            self,
            output=None,
            status=TaskStatus.COMPLETE,
            error_msg=None,
            evaluation_score: float | None = None,
            evaluation_metadata: dict | None = None,
    ):
        evaluation_result = self.task.evaluate_execution(
            implementation_version=self.implementation_version,
            crashed=bool(error_msg) or TaskStatus.FAILED.eq(status),
            score=evaluation_score,
            metadata=evaluation_metadata,
        )
        
        with transaction.atomic():
            TaskExecution.objects.filter(id=self.id).update(
                status=status,
                error_msg=error_msg,
                output=output or {},
                executed_time=common.get_now(),
                evaluation_score=evaluation_result.score,
                evaluation_metadata=evaluation_result.metadata,
            )
        
        self.refresh_from_db()
        return self


class TaskImplementationVersion(BaseErieIronModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    source_kind = models.TextField(choices=TaskImplementationSourceKind.choices())
    version_number = models.PositiveIntegerField()
    application_repo_file_path = models.TextField(null=True, blank=True)
    application_repo_ref = models.TextField(null=True, blank=True)
    source_metadata = models.JSONField(default=dict, encoder=ErieIronJSONEncoder, blank=True)
    evaluator_config = models.JSONField(default=dict, encoder=ErieIronJSONEncoder, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ["task", "version_number"]
        indexes = [
            models.Index(fields=["task", "source_kind"]),
            models.Index(fields=["task", "created_at"]),
        ]
    
    def __str__(self):
        return f"{self.task_id}::{self.source_kind}::v{self.version_number}"
    
    def source_kind_enum(self) -> TaskImplementationSourceKind:
        return TaskImplementationSourceKind(self.source_kind)
    
    def clean(self):
        errors = {}
        if not TaskImplementationSourceKind.valid(self.source_kind):
            raise ValidationError({"source_kind": "invalid source kind"})
        
        source_kind = self.source_kind_enum()
        task_source_kind = self.task.get_implementation_source_kind_enum() if self.task_id else None
        if task_source_kind and task_source_kind.neq(source_kind):
            errors["source_kind"] = "implementation versions must match the task implementation source kind"
        
        application_repo_file_path = common.default_str(self.application_repo_file_path).strip()
        application_repo_ref = common.default_str(self.application_repo_ref).strip()
        
        if not application_repo_file_path:
            errors["application_repo_file_path"] = (
                f"{source_kind.label()} implementations require application_repo_file_path"
            )
        if not application_repo_ref:
            errors["application_repo_ref"] = (
                f"{source_kind.label()} implementations require application_repo_ref"
            )
        
        evaluator_config = copy.deepcopy(self.evaluator_config or {})
        if evaluator_config:
            if not common.default_str(evaluator_config.get("application_repo_file_path")).strip():
                errors["evaluator_config"] = "custom evaluators require application_repo_file_path"
            elif not common.default_str(evaluator_config.get("application_repo_ref")).strip():
                errors["evaluator_config"] = "custom evaluators require application_repo_ref"
        
        if errors:
            raise ValidationError(errors)
    
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    
    def get_source_label(self) -> str:
        return common.default_str(self.application_repo_file_path)

    def get_evaluator_label(self) -> str:
        evaluator_file_path = common.default_str(
            (self.evaluator_config or {}).get("application_repo_file_path")
        )
        if evaluator_file_path:
            return evaluator_file_path
        return "Default"

    def get_source_file_path(self, repo_root: Path) -> Path:
        return self._resolve_repo_file_path(repo_root, self.application_repo_file_path)

    def get_output_schema_file_path(self, repo_root: Path) -> Optional[Path]:
        schema_path = common.default_str((self.source_metadata or {}).get("output_schema_file_path")).strip()
        if not schema_path:
            return None
        return self._resolve_repo_file_path(repo_root, schema_path)

    def get_evaluator_file_path(self, repo_root: Path) -> Optional[Path]:
        file_path = common.default_str((self.evaluator_config or {}).get("application_repo_file_path")).strip()
        if not file_path:
            return None
        return self._resolve_repo_file_path(repo_root, file_path)

    def get_evaluator_output_schema_file_path(self, repo_root: Path) -> Optional[Path]:
        schema_path = common.default_str((self.evaluator_config or {}).get("output_schema_file_path")).strip()
        if not schema_path:
            return None
        return self._resolve_repo_file_path(repo_root, schema_path)

    @staticmethod
    def _resolve_repo_file_path(repo_root: Path, relative_path: str | None) -> Path:
        repo_root = Path(repo_root).resolve()
        target_path = (repo_root / common.assert_not_empty(common.default_str(relative_path).strip())).resolve()
        if repo_root not in target_path.parents and target_path != repo_root:
            raise ValidationError("implementation file path must stay within the application repo")
        return target_path
    
    def get_evaluator_spec(self) -> dict[str, Any]:
        evaluator_config = copy.deepcopy(self.evaluator_config or {})
        if not evaluator_config:
            return {
                "kind": "default",
                "config": {},
            }
        return {
            "kind": self.source_kind,
            "config": evaluator_config,
        }
    
    def build_execution_provenance(self, business: Business) -> dict[str, Any]:
        provenance = {
            "source_version_number": self.version_number,
            "source_kind": self.source_kind,
            "application_repo_file_path": self.application_repo_file_path,
            "application_repo_ref": self.application_repo_ref,
            "instance_config_revision": None,
            "source_metadata": copy.deepcopy(self.source_metadata or {}),
        }
        
        if self.application_repo_file_path and self.application_repo_ref:
            application_repo_url = business.get_application_repo_url()
            application_repo_revision, _ = GitWrapper.get_commit_for_ref(
                application_repo_url,
                self.application_repo_ref,
            )
            provenance.update(
                {
                    "application_repo_url": application_repo_url,
                    "application_repo_revision": application_repo_revision,
                    "instance_config_revision": application_repo_revision,
                }
            )
        
        return provenance


class SelfDrivingTask(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    main_name = models.TextField(null=False)
    test_file_path = models.TextField(null=True)
    sandbox_path = models.TextField(null=False)
    goal = models.TextField(null=False)
    task = models.OneToOneField(
        "Task", on_delete=models.SET_NULL, null=True, blank=True, db_index=True
    )
    initial_tests_pass = models.BooleanField(null=False, default=False)
    iteration_mode = models.CharField(
        max_length=50,
        choices=IterationMode.choices(),
        default=IterationMode.LOCAL_TESTS.value,
        null=True,
        blank=True,
        help_text="Current execution stage: local tests, container tests, or AWS deployment"
    )
    config_path = models.TextField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    latest_phase_change_at = models.DateTimeField(null=True)
    phase_change_seq = models.IntegerField(default=0)
    
    def get_readonly_files(self):
        readonly_file_paths = [f for f in settings.READONLY_FILES]
        
        if False and self.initial_tests_pass:
            test_file_path = self.test_file_path
            
            q = self.business.codefile_set.filter(
                Q(file_path__contains="test/") | Q(file_path__contains="/test")
            )
            if test_file_path:
                q = q.exclude(file_path=test_file_path)
            
            for test_file in q.order_by("file_path").distinct("file_path"):
                readonly_file_paths.append(
                    {
                        "path": test_file.file_path,
                        "alternatives": "none",
                        "description": "This is an existing test that asserts another tasks behavior.  This test must never be modifified.  If this test is failing, that means the code you wrote for this task caused a regression",
                    }
                )
        
        return readonly_file_paths
    
    def get_git(self) -> GitWrapper:
        return GitWrapper(self.sandbox_path)
    
    def rollback_to(self, iteraton: "SelfDrivingTaskIteration"):
        # we don't want any files created in future iterations hanging around, so delete everything
        # there's prob a more efficient way to do this, but this is fine for now
        for cf in CodeFile.objects.filter(codeversion__task_iteration__task=self):
            common.quietly_delete(
                common.assert_in_sandbox(self.sandbox_path, cf.file_path)
            )
        
        for i in self.selfdrivingtaskiteration_set.order_by("timestamp"):
            i.write_to_disk()
            if i.id == iteraton.id:
                break
    
    def get_best_iteration(self) -> "SelfDrivingTaskIteration":
        best = self.selfdrivingtaskbestiteration_set.order_by("timestamp").last()
        
        return best.iteration if best else self.get_most_recent_iteration()
    
    def get_most_recent_iteration(self) -> "SelfDrivingTaskIteration":
        return self.selfdrivingtaskiteration_set.order_by("timestamp").last()
    
    def get_active_iteration(
            self, *, create_if_missing: bool = True
    ) -> Optional["SelfDrivingTaskIteration"]:
        iteration = self.get_most_recent_iteration()
        if iteration or not create_if_missing:
            return iteration
        
        with transaction.atomic():
            iteration = self.get_most_recent_iteration()
            if iteration:
                return iteration
            iteration = SelfDrivingTaskIteration.objects.create(
                self_driving_task=self,
                version_number=1,
                planning_model="",
                coding_model="",
            )
        return iteration
    
    def get_most_recent_code_version(self) -> Optional["CodeVersion"]:
        last_iteration = self.get_most_recent_iteration()
        if last_iteration:
            last_code_version: CodeVersion = last_iteration.codeversion_set.first()
            if last_code_version:
                return last_code_version
        
        return None
    
    def get_most_recent_log_contents(self) -> Optional[str]:
        last_iteration = self.get_most_recent_iteration()
        if last_iteration:
            return last_iteration.log_content_execution
        else:
            return None
    
    def get_cost(self) -> float:
        result = LlmRequest.objects.filter(
            task_iteration__self_driving_task=self
        ).aggregate(total=Sum("price"))
        return result["total"] or 0.0
    
    def iterate(self) -> "SelfDrivingTaskIteration":
        iteration_to_modify = None
        most_recent_iteration = self.get_most_recent_iteration()
        
        try:
            iteration_to_modify = SelfDrivingTaskIteration.objects.get(
                id=most_recent_iteration.evaluation_json.get("iteration_id_to_modify")
            )
        except:
            ...
        
        if not iteration_to_modify:
            iteration_to_modify = most_recent_iteration
        
        max_version = (
                SelfDrivingTaskIteration.objects.filter(self_driving_task=self).aggregate(
                    models.Max("version_number")
                )["version_number__max"]
                or 0
        )
        
        with transaction.atomic():
            current_iteration = SelfDrivingTaskIteration.objects.create(
                self_driving_task=self, version_number=max_version + 1
            )
        
        if not iteration_to_modify:
            iteration_to_modify = current_iteration
        
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            start_iteration=(
                iteration_to_modify
                if iteration_to_modify.id != current_iteration.id
                else None
            )
        )
        
        return current_iteration
    
    def get_require_tests(self) -> bool:
        return self.task and self.task.requires_test
    
    def get_sandbox(self) -> Path:
        return Path(self.sandbox_path)
    
    def get_task_identifier_fragment(self) -> str:
        task_id = getattr(self, "task_id", None)
        if task_id is not None:
            return str(task_id).replace("task_", "")
        return str(getattr(self.task, "id", "task"))


class SelfDrivingTaskIteration(BaseErieIronModel):
    self_driving_task = models.ForeignKey(
        SelfDrivingTask, on_delete=models.CASCADE, null=True
    )
    start_iteration = models.ForeignKey(
        "SelfDrivingTaskIteration", on_delete=models.CASCADE, null=True
    )
    achieved_goal = models.BooleanField(null=False, default=False)
    version_number = models.IntegerField(null=False, default=0)
    test_module = models.TextField(null=True)
    execute_module = models.TextField(null=True)
    planning_model = models.TextField()
    coding_model = models.TextField()
    docker_tag = models.TextField(null=True)
    log_content_execution = models.TextField(null=True)
    log_content_coding = models.TextField(null=True)
    log_content_evaluation = models.TextField(null=True)
    log_content_init = models.TextField(null=True)
    log_content_deployment = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    log_content_cloudwatch = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    exceptions = models.TextField(null=True)
    
    planning_json = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    evaluation_json = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    slowest_cloudformation_resources = models.JSONField(
        null=True, encoder=ErieIronJSONEncoder
    )
    routing_json = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    strategic_unblocking_json = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def get_all_log_content(self):
        return "\n\n".join(
            common.filter_none(
                [
                    self.log_content_init,
                    self.log_content_coding,
                    self.log_content_execution,
                    self.log_content_evaluation,
                ]
            )
        )
    
    def get_previous_iteration(self) -> "SelfDrivingTaskIteration":
        return (
            self.self_driving_task.selfdrivingtaskiteration_set.filter(
                timestamp__lt=self.timestamp
            )
            .order_by("timestamp")
            .last()
        )
    
    def get_next_iteration(self) -> "SelfDrivingTaskIteration":
        return (
            self.self_driving_task.selfdrivingtaskiteration_set.filter(
                timestamp__gt=self.timestamp
            )
            .order_by("timestamp")
            .first()
        )
    
    def get_relevant_iterations(
            self,
    ) -> tuple["SelfDrivingTaskIteration", "SelfDrivingTaskIteration"]:
        previous_iteration = None
        iteration_to_modify = None
        
        previous_iteration = self.get_previous_iteration_with_eval()
        if not previous_iteration:
            previous_iteration = self
        
        iteration_to_modify = self.start_iteration
        if not iteration_to_modify:
            iteration_to_modify = previous_iteration
        
        if not iteration_to_modify:
            iteration_to_modify = self
        
        return previous_iteration, iteration_to_modify
    
    def get_latest_execution(self) -> TaskExecution:
        te = self.taskexecution_set.last()
        
        if te:
            return te
        else:
            return self.self_driving_task.task.create_execution(iteration=self)
    
    def get_total_price(self) -> Tuple[float, int]:
        return self.llmrequest_set.aggregate(total_price=Sum("price"))["total_price"]
    
    def get_llm_cost(self) -> Tuple[float, int]:
        totals = self.llmrequest_set.aggregate(
            total_price=Sum("price"), total_tokens=Sum("token_count")
        )
        return totals["total_price"] or 0, totals["total_tokens"] or 0
    
    def write_to_disk(self):
        self_driving_task = self.self_driving_task
        
        sandbox_path = self_driving_task.get_sandbox()
        business = self_driving_task.business
        
        for code_file in business.codefile_set.exclude(
                file_path__in=[self_driving_task.test_file_path]
        ).order_by("file_path"):
            if code_file.file_path.startswith("/"):
                logging.error(
                    f"code got indexed with a root path!: {code_file.file_path}"
                )
                continue
            
            if code_file.file_path.startswith(str(os.getcwd())):
                logging.error(f"erie iron code got indexed!: {code_file.file_path}")
                continue
            
            code_version = code_file.get_version(self, default_to_latest=True)
            first_version = code_file.codeversion_set.order_by("created_at").first()
            if code_version:
                code_version.write_to_disk(sandbox_path)
            elif first_version and code_file.allow_autonomous_delete():
                # Detect if this code_file was introduced:
                #  1. in this Task
                first_version_in_this_task = first_version.task_iteration.self_driving_task_id == self.self_driving_task_id
                
                #  2. in an iteration that's **after** the self iteration
                first_version_iteration_after_this_iteration = first_version.task_iteration.version_number > self.version_number
                
                # If both conditions are met, then this file should be deleted
                if first_version_in_this_task and first_version_iteration_after_this_iteration:
                    # File was introduced after this iteration in the same task
                    common.quietly_delete(sandbox_path / code_file.get_path())
                    logging.info(
                        f"{code_file.get_path()} was introduced after iteration {self.id}. Deleted from disk."
                    )
                else:
                    logging.info(
                        f"{code_file.get_path()} did not exist at iteration {self.id}.  NOT removing from disk.  first_version_in_this_task={first_version_in_this_task}; first_version_iteration_after_this_iteration={first_version_iteration_after_this_iteration}"
                    )
    
    def get_code_version(self, code_file: "CodeFile"):
        if isinstance(code_file, Path):
            code_file = CodeFile.get(
                self.self_driving_task.business, self.get_relative_path(code_file)
            )
        
        code_version_to_modify = code_file.get_version(self)
        
        if not code_version_to_modify:
            code_version_to_modify = code_file.get_latest_version()
        
        if not code_version_to_modify:
            code_version_to_modify = code_file.init_from_codefile(
                self, code_file.file_path
            )
        
        return code_version_to_modify
    
    def get_relative_path(self, code_file):
        try:
            return Path(code_file).relative_to(self.self_driving_task.sandbox_path)
        except:
            return code_file
    
    def get_previous_iteration_with_eval(self) -> "SelfDrivingTaskIteration":
        return (
            SelfDrivingTaskIteration.objects.filter(
                self_driving_task=self.self_driving_task,
                evaluation_json__isnull=False,
                timestamp__lt=self.timestamp,
            )
            .order_by("-timestamp")
            .first()
        )
    
    def has_error(self) -> bool:
        eval_json = (self.evaluation_json or {})
        return any(s in eval_json for s in ["error", "test_errors"])
    
    def get_unit_test_errors(self) -> list[dict]:
        return common.ensure_list(common.get(self, ["evaluation_json", "test_errors"]))
    
    def get_error_llm_msg(self, label: str) -> list[LlmMessage]:
        d = {
            "iteration_id": self.id,
            "iteration_version": self.version_number,
        }
        
        if "error" in self.evaluation_json:
            d["error"] = self.evaluation_json.get("error")
        
        if "test_errors" in self.evaluation_json:
            d["test_errors"] = self.evaluation_json.get("test_errors")
        
        return LlmMessage.user_from_data(label, d)
    
    def get_error(self) -> tuple[str, str]:
        evaluation_json = self.evaluation_json
        if evaluation_json is None:
            return None, None
        
        evaluation_json = evaluation_json or {}
        
        if "error" in evaluation_json:
            error_info = evaluation_json.get("error")
            
            if isinstance(error_info, dict):
                return error_info.get("summary"), error_info.get("logs")
            else:
                return evaluation_json.get("summary"), error_info
        else:
            error_info = common.first(evaluation_json.get("evaluation", []))
            if error_info:
                return error_info.get("summary"), error_info.get("details")
            else:
                return "unknown", "unknown"
    
    def goal_achieved(self):
        return common.parse_bool(
            common.get(self, ["evaluation_json", "goal_achieved"], False)
        )
    
    def get_llm_data(self, description, include_details=False):
        code_changes = []
        for cv in self.codeversion_set.all().order_by("created_at"):
            diff = cv.get_diff()
            if diff:
                code_changes.append(
                    {"file_name": cv.code_file.file_path, "changes_diff": diff}
                )
        
        d = {
            "description": description,
            "iteration_id": self.id,
            "iteration_version_number": self.version_number,
            "iteration_timestamp": self.timestamp,
            "post_code_changes_execution_evaluation": {
                "summary": self.evaluation_json.get("summary"),
                "runtime_errors": self.evaluation_json.get("error"),
                "test_errors": self.evaluation_json.get("test_errors"),
            },
            "important_reminder": "Learn from the past.  If there are errors, make every attempt to not repeat them",
        }
        
        if include_details:
            d.update(
                {
                    "pre_planning_routing": self.routing_json,
                    "pre_coding_planning": self.planning_json,
                    "code_changes": code_changes,
                    "sysout": self.log_content_execution or "N/A",
                }
            )
        
        return d
    
    def get_all_code_versions(self):
        return {
            cv.code_file_id: cv
            for cv in self.codeversion_set.all().order_by("created_at")
        }.values()


class SelfDrivingTaskBestIteration(BaseErieIronModel):
    task = models.ForeignKey(SelfDrivingTask, on_delete=models.CASCADE, null=True)
    iteration = models.ForeignKey(
        SelfDrivingTaskIteration, on_delete=models.CASCADE, null=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)


class RunningProcess(BaseErieIronModel):
    task_execution = models.OneToOneField(
        TaskExecution,
        on_delete=models.CASCADE,
        related_name="process",
        null=True,
        blank=True,
    )
    process_id = models.IntegerField(null=True, blank=True)
    container_id = models.TextField(null=True, blank=True)  # For docker processes
    execution_type = models.TextField(
        max_length=20, choices=[("local", "Local"), ("docker", "Docker")]
    )
    log_file_path = models.TextField(null=True, blank=True)
    log_tail = models.TextField(blank=True, default="")  # Store last ~1000 chars of log
    started_at = models.DateTimeField(auto_now_add=True)
    is_running = models.BooleanField(default=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    
    def update_log_tail(self, max_chars=100000):
        """Update the log_tail field with the latest log content"""
        if self.log_file_path and Path(self.log_file_path).exists():
            try:
                with open(self.log_file_path, "r") as f:
                    content = f.read()
                    self.log_tail = (
                        content[-max_chars:] if len(content) > max_chars else content
                    )
                    self.save(update_fields=["log_tail"])
            except Exception as e:
                logging.warning(f"Failed to update log tail for process {self.id}: {e}")
    
    def kill_process(self):
        """Kill the running process"""
        import signal
        import os
        
        if not self.is_running:
            return False
        
        self.is_running = False
        self.terminated_at = common.get_now()
        self.save(update_fields=["is_running", "terminated_at"])
        
        try:
            if self.execution_type == "docker" and self.container_id:
                # Kill docker container
                subprocess.run(["docker", "kill", self.container_id], check=True)
            elif self.execution_type == "local" and self.process_id:
                # Kill local process
                os.kill(self.process_id, signal.SIGTERM)
            
            return True
        except Exception as e:
            logging.warning(f"Failed to kill process {self.id}: {e}")
            return False


class LlmRequest(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.SET_NULL, null=True)
    initiative = models.ForeignKey(Initiative, on_delete=models.SET_NULL, null=True)
    task_iteration = models.ForeignKey(
        SelfDrivingTaskIteration, on_delete=models.SET_NULL, null=True
    )
    token_count = models.IntegerField()
    chat_millis = models.IntegerField(default=0)
    title = models.TextField(null=True, default="Unknown")
    price = models.FloatField()
    llm_model = models.TextField(null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    input_messages = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    resp_json = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    response = models.TextField(null=True)
    reasoning_effort = models.TextField(null=True)
    verbosity = models.TextField(null=True)
    creativity = models.TextField(null=True)
    output_schema = models.TextField(null=True)
    
    def get_llm_data(self):
        return {
            "model": self.llm_model,
            "verbosity": self.verbosity,
            "reasoning_effort": self.reasoning_effort,
            "input_messages": self.input_messages,
            "llm_response": self.response,
        }


class CodeFile(BaseErieIronModel):
    class Meta:
        unique_together = ["business", "file_path"]
    
    ARCHITECTURE_FILE = "docs/architecture.md"
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    
    # file_path is not the primary key because code file paths change as we refactor
    file_path = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.file_path} - {self.id}"
    
    def allow_autonomous_delete(self) -> bool:
        p = (self.file_path or "").strip()
        
        if p.startswith(("venv", "./venv", "env", "./env")):
            return False
        
        return p != CodeFile.ARCHITECTURE_FILE
    
    def get_path(self) -> Path:
        return Path(self.file_path)
    
    def get_base_name(self) -> Path:
        return common.get_basename(self.get_path())
    
    def get_dir(self) -> Path:
        return self.get_path().parent
    
    def get_latest_code(self):
        code_version = self.codeversion_set.order_by("-created_at").first()
        if code_version:
            return code_version.code
        else:
            return ""
    
    def get_latest_version(
            self, self_driving_task: SelfDrivingTask = None
    ) -> "CodeVersion":
        if self_driving_task:
            code_version = (
                self.codeversion_set.filter(
                    task_iteration__self_driving_task=self_driving_task
                )
                .order_by("created_at")
                .last()
            )
            
            return code_version if code_version else self.get_latest_version()
        else:
            return self.codeversion_set.order_by("created_at").last()
    
    def get_version(
            self, iteration: SelfDrivingTaskIteration, default_to_latest=False
    ) -> Optional["CodeVersion"]:
        code_version = (
            self.codeversion_set.filter(task_iteration=iteration)
            .order_by("created_at")
            .last()
        )
        
        if code_version:
            return code_version
        
        if default_to_latest:
            return self.get_latest_version()
        
        # Find the code version from the iteration that is closest without being after this iteration
        # This gets the "last known version" of this file at the time of the given iteration
        closest_version = (
            self.codeversion_set.filter(
                task_iteration__self_driving_task=iteration.self_driving_task,
                task_iteration__timestamp__lte=iteration.timestamp,
            )
            .order_by("-task_iteration__timestamp")
            .first()
        )
        
        return closest_version
    
    @staticmethod
    def get(business: Business, relative_path: Path) -> "CodeFile":
        if str(relative_path).startswith("/"):
            raise Exception(
                f"CodeFile needs to be a relative path.  got {relative_path}"
            )
        
        return CodeFile.objects.get_or_create(
            business=business, file_path=relative_path
        )[0]
    
    @staticmethod
    def init_from_codefile(
            task_iteration: SelfDrivingTaskIteration, relative_file_path: Path
    ) -> "CodeVersion":
        sandbox_path = Path(task_iteration.self_driving_task.sandbox_path)
        file_path = sandbox_path / relative_file_path
        file_path = common.assert_exists(file_path)
        
        if str(file_path).startswith("/"):
            asdf = 1
        
        return CodeFile.update_from_path(
            task_iteration,
            file_path,
            code_instructions=f"initial code from existing file",
        )
    
    def update(
            self,
            task_iteration: SelfDrivingTaskIteration,
            code: str,
            code_instructions=None,
    ):
        if code is None:
            asdfasdf = 1
        
        cv = CodeVersion.objects.create(
            task_iteration=task_iteration,
            code_file=self,
            code_instructions=code_instructions,
            code=code,
        )
        
        file_path = Path(task_iteration.self_driving_task.sandbox_path) / self.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code)
        
        return cv
    
    @staticmethod
    def update_from_path(
            task_iteration: SelfDrivingTaskIteration,
            abs_file_path: Path,
            code_instructions=None,
    ) -> "CodeVersion":
        abs_file_path = common.assert_exists(abs_file_path)
        
        self_driving_task = task_iteration.self_driving_task
        self_driving_task.refresh_from_db(fields=["sandbox_path"])
        
        business = self_driving_task.business
        
        code = abs_file_path.read_text()
        with transaction.atomic():
            relative_path = abs_file_path.relative_to(self_driving_task.sandbox_path)
            code_file = CodeFile.get(business, relative_path)
            
            return code_file.update(
                task_iteration=task_iteration,
                code=code,
                code_instructions=code_instructions,
            )
    
    def get_version_for_iteration(
            self,
            iteration: SelfDrivingTaskIteration
    ) -> "CodeVersion":
        return self.get_version(iteration) \
            or self.get_latest_version() \
            or self.init_from_codefile(
                iteration,
                common.assert_exists(Path(iteration.self_driving_task.sandbox_path) / self.file_path)
            )


class CodeVersion(BaseErieIronModel):
    code_file = models.ForeignKey(CodeFile, on_delete=models.CASCADE)
    task_iteration = models.ForeignKey(
        SelfDrivingTaskIteration, on_delete=models.CASCADE
    )
    code_instructions = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    codebert_embedding = VectorField(dimensions=768, null=True)
    
    def has_diff(self) -> bool:
        previous_version = (
            CodeVersion.objects.filter(
                code_file=self.code_file, created_at__lt=self.created_at
            )
            .order_by("-created_at")
            .first()
        )
        
        if not previous_version:
            return False
        
        return common.is_not_empty(self.get_diff())
    
    def get_diff(self) -> str:
        try:
            previous_version = (
                CodeVersion.objects.filter(
                    code_file=self.code_file, created_at__lt=self.created_at
                )
                .order_by("-created_at")
                .first()
            )
            
            file_name = os.path.basename(self.code_file.get_path())
            
            diff_lines = difflib.unified_diff(
                common.default_str(previous_version.code).splitlines(),
                common.default_str(self.code).splitlines(),
                fromfile=f"old_{file_name}",
                tofile=f"new_{file_name}",
                lineterm="",
            )
            return "\n".join(diff_lines)
        except:
            return ""
    
    def write_to_disk(self, sandbox_root_dir=None) -> Path:
        if not sandbox_root_dir:
            sandbox_root_dir = self.task_iteration.self_driving_task.sandbox_path
        
        file_path = Path(sandbox_root_dir) / self.code_file.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(self.code)
        
        return file_path
    
    def get_llm_message_data(self, include_diff=True) -> dict:
        code_file = self.code_file
        d = {
            "file_path": code_file.file_path,
            "code": self.code,
        }
        
        if include_diff:
            d["diff_against_previous_version"] = self.get_diff()
        
        return d


class CodeMethod(BaseErieIronModel):
    code_version = models.ForeignKey(CodeVersion, on_delete=models.CASCADE)
    name = models.TextField()
    parameters = models.JSONField(default=dict)
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    codebert_embedding = VectorField(dimensions=768, null=True)


@receiver(pre_delete, sender=SelfDrivingTaskIteration)
def kill_running_processes_on_iteration_delete(sender, instance, **kwargs):
    """
    Kill any running processes associated with this iteration before deletion.
    """
    running_processes = RunningProcess.objects.filter(
        task_execution__iteration=instance, is_running=True
    )
    
    for process in running_processes:
        try:
            process.kill_process()
            logging.info(
                f"Killed running process {process.id} for iteration {instance.id}"
            )
        except Exception as e:
            logging.warning(
                f"Failed to kill process {process.id} for iteration {instance.id}: {e}"
            )


class AgentTombstone(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.SET_NULL, null=True)
    name = models.TextField()
    data_json = models.JSONField(encoder=ErieIronJSONEncoder)
    timestamp = models.DateTimeField(auto_now_add=True)


class AgentLesson(BaseErieIronModel):
    source_iteration = models.ForeignKey(
        SelfDrivingTaskIteration, on_delete=models.SET_NULL, null=True
    )
    agent_step = models.TextField()
    pattern = models.TextField()
    invalid_lesson = models.BooleanField(default=False, null=True)
    trigger = models.TextField()
    lesson = models.TextField()
    context_tags = models.JSONField(default=list)
    embedding = VectorField(dimensions=384, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    @staticmethod
    def create_from_data(
            agent_step, data: dict, source_iteration=None
    ) -> "AgentLesson":
        tag_text = common.safe_join(data.get("context_tags", []), delim=",")
        text = f'Step: {agent_step}. {data.get("pattern_description")}. {data.get("trigger")}. {data.get("lesson")}. Tags: {tag_text}'
        
        from erieiron_common.chat_engine import language_utils
        
        embedding = language_utils.get_text_embedding(text)
        
        lesson = AgentLesson.objects.create(
            source_iteration=source_iteration,
            agent_step=agent_step,
            pattern=data.get("pattern_description"),
            trigger=data.get("trigger"),
            lesson=data.get("lesson"),
            context_tags=data.get("context_tags"),
            embedding=embedding,
        )
    
    def get_llm_data(self):
        return {
            "agent_step": self.agent_step,
            "valid_lesson": not self.invalid_lesson,
            "pattern": self.pattern,
            "trigger": self.trigger,
            "lesson": self.lesson,
            "context_tags": self.context_tags,
        }


class BusinessConversation(BaseErieIronModel):
    """Tracks conversations between users and the system about a specific business"""
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='conversations')
    initiative = models.ForeignKey('Initiative', on_delete=models.SET_NULL, null=True, blank=True,
                                   help_text="Optional: Scope conversation to a specific initiative")
    title = models.TextField(help_text="Auto-generated summary of conversation topic")
    status = models.TextField(default='active', choices=[
        ('active', 'Active'),
        ('archived', 'Archived'),
        ('led_to_change', 'Led to Change')
    ])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"{self.business.name} - {self.title}"
    
    def get_context_snapshot(self) -> dict:
        """Gather all relevant business context for LLM"""
        business_data = self.business.get_llm_data()
        
        # Add initiative-specific context if scoped
        if self.initiative:
            business_data['initiative'] = {
                'description': self.initiative.description,
                'type': self.initiative.initiative_type,
            }
        
        # Add recent active tasks
        recent_tasks = Task.objects.filter(
            initiative__business=self.business,
            status__in=[TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]
        ).order_by('-created_timestamp')[:10]
        business_data['active_tasks'] = [
            {'name': t.get_name(), 'status': t.status, 'description': t.description}
            for t in recent_tasks
        ]
        
        # Add infrastructure stack info
        stacks = InfrastructureStack.objects.filter(business=self.business)
        business_data['infrastructure_stacks'] = [
            {'name': s.stack_name, 'type': s.stack_type, 'status': "", 'environment': s.env_type}
            for s in stacks
        ]
        
        return business_data


class ConversationMessage(BaseErieIronModel):
    """Individual messages within a conversation"""
    conversation = models.ForeignKey('BusinessConversation', on_delete=models.CASCADE, related_name='messages')
    role = models.TextField(choices=[
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('system', 'System')
    ])
    content = models.TextField()
    llm_request = models.ForeignKey('LlmRequest', on_delete=models.SET_NULL, null=True, blank=True, help_text="Track API usage for this message")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."


class ConversationChange(BaseErieIronModel):
    """Tracks proposed changes from conversations (Phase 2+)"""
    conversation = models.ForeignKey('BusinessConversation', on_delete=models.CASCADE, related_name='changes')
    message = models.ForeignKey('ConversationMessage', on_delete=models.CASCADE,
                                help_text="The assistant message that proposed this change")
    change_type = models.TextField(choices=[
        ('business_plan', 'Business Plan'),
        ('architecture', 'Architecture'),
        ('infrastructure', 'Infrastructure'),
        ('initiative', 'New Initiative'),
        ('task', 'New Task')
    ])
    change_description = models.TextField(help_text="Human-readable description of proposed change")
    change_details = models.JSONField(encoder=ErieIronJSONEncoder,
                                      help_text="Structured details of what will change")
    approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    applied = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)
    resulting_tasks = models.ManyToManyField('Task', blank=True,
                                             help_text="Tasks created to implement this change")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class OAuthAccount(models.Model):
    """
    Links external OAuth provider accounts (e.g., Cognito/Google) to Django users.

    When a user authenticates via Cognito, we create an OAuthAccount record
    that maps their Cognito 'sub' claim to a Django User. This allows:
    - Multiple OAuth providers per user (future extensibility)
    - Tracking when users last authenticated
    - Storing raw OAuth profile data for debugging
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='oauth_accounts',
        help_text="Django user linked to this OAuth account"
    )

    provider = models.CharField(
        max_length=64,
        help_text="OAuth provider name (e.g., 'cognito-google')"
    )

    external_id = models.CharField(
        max_length=255,
        help_text="Provider's unique user ID (e.g., Cognito 'sub' claim)"
    )

    raw_profile = models.JSONField(
        default=dict,
        help_text="Raw OAuth claims/profile data from provider"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'erieiron_oauth_account'
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'external_id'],
                name='unique_oauth_provider_external_id'
            )
        ]
        indexes = [
            models.Index(fields=['provider', 'external_id']),
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f"{self.provider}:{self.external_id} -> User {self.user_id}"
