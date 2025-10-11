import difflib
# Load model once at startup
import logging
import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Tuple, Optional

from django.db import models, transaction
from django.db.models import Sum, Q
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver
from pgvector.django import VectorField
from sentence_transformers import SentenceTransformer

import settings
from botocore.exceptions import ClientError
from erieiron_autonomous_agent.enums import BusinessStatus, BusinessGuidanceRating, TrafficLight, TaskStatus
from erieiron_autonomous_agent.utils import codegen_utils
from erieiron_autonomous_agent.utils.codegen_utils import extract_methods
from erieiron_common import common
from erieiron_common.enums import Level, LlcStructure, TaskExecutionSchedule, InitiativeType, GoalStatus, BusinessIdeaSource, TaskType, AwsEnv
from erieiron_common.git_utils import GitWrapper
from erieiron_common.json_encoder import ErieIronJSONEncoder
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import BaseErieIronModel

mini_lm_model = SentenceTransformer("all-MiniLM-L6-v2")


class Business(BaseErieIronModel):
    name = models.TextField(unique=True)
    source = models.TextField(null=False, choices=BusinessIdeaSource.choices())
    status = models.TextField(default=BusinessStatus.IDEA, choices=BusinessStatus.choices())
    
    service_token = models.TextField(null=True)
    summary = models.TextField(null=True)
    raw_idea = models.TextField(null=True)
    bank_account_id = models.TextField(null=True)
    business_plan = models.TextField(null=True)
    architecture = models.TextField(null=True)
    value_prop = models.TextField(null=True)
    revenue_model = models.TextField(null=True)
    audience = models.TextField(null=True)
    required_credentials = models.JSONField(null=True)
    core_functions = models.JSONField(default=list)
    execution_dependencies = models.JSONField(default=list)
    growth_channels = models.JSONField(default=list)
    personalization_options = models.JSONField(default=list)
    allow_autonomous_shutdown = models.BooleanField(default=True)
    needs_domain = models.BooleanField(default=False)
    web_container_cpu = models.PositiveIntegerField(default=512)
    web_container_memory = models.PositiveIntegerField(default=1024)
    web_desired_count = models.PositiveIntegerField(default=1)
    autonomy_level = models.TextField(null=True, choices=Level.choices())
    domain = models.TextField(null=True)
    github_repo_url = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def get_readonly_files(self, path_force_writable=None):
        q = self.codefile_set.filter(
            Q(file_path__contains="test/") | Q(file_path__contains="/test")
        )
        if path_force_writable:
            q = q.exclude(file_path=path_force_writable)
        
        readonly_file_paths = [f for f in settings.READONLY_FILES]
        for test_file in q.order_by("file_path").distinct("file_path"):
            readonly_file_paths.append(
                {
                    "path": test_file.file_path,
                    "alternatives": path_force_writable,
                    "description": "This is an existing test that asserts another tasks behavior.  This test must never be modifified.  If this test is failing, that means the code you wrote for this task caused a regression"
                }
            )
        return readonly_file_paths
    
    def get_existing_required_credentials_llmm(self) -> list[LlmMessage]:
        return LlmMessage.user_from_data("Existing Required Credentials.  Use for reference.  Not need to re-specify.", {
            "required_credentials": self.required_credentials or {}
        })
    
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
            "business_analysis": business_analysis.summary if business_analysis else None
        }
    
    def get_iam_role_name(self):
        return f"erieiron-{self.service_token}-role"
    
    def get_latest_board_guidance(self):
        return BusinessGuidance.objects.filter(business=self).order_by("created_timestamp").last()
    
    def get_latest_capacity(self):
        return BusinessCapacityAnalysis.objects.filter(business=self).order_by("created_timestamp").last()
    
    def get_latest_analysist(self) -> tuple['BusinessAnalysis', 'BusinessLegalAnalysis']:
        return (BusinessAnalysis.objects.filter(business=self).order_by("created_timestamp").last(),
                BusinessLegalAnalysis.objects.filter(business=self).order_by("created_timestamp").last())
    
    @staticmethod
    def get_erie_iron_business() -> 'Business':
        return Business.objects.get_or_create(
            name="Erie Iron, LLC",
            defaults={
                "source": BusinessIdeaSource.HUMAN
            }
        )[0]
    
    def needs_bank_balance_update(self):
        return not BusinessBankBalanceSnapshot.objects.filter(business=self, created_timestamp__gt=common.get_now() - timedelta(days=1)).exists()
    
    def needs_capacity_analysis(self):
        """
        Returns True if no BusinessCapacityAnalysis has been created in the past 1 hours.
        """
        return not BusinessCapacityAnalysis.objects.filter(business=self, created_timestamp__gt=common.get_now() - timedelta(hours=1)).exists()
    
    def needs_analysis(self):
        """
        Returns True if no BusinessAnalysis has been created in the past 14 days.
        """
        return not BusinessAnalysis.objects.filter(business=self, created_timestamp__gt=common.get_now() - timedelta(days=14)).exists()
    
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
                    "status": "HAS_CAPACITY"
                }
            ],
            "total_human_capacity_hours": 18,
            "total_pending_task_hours": 0,
            "capacity_utilization_percent": 0
        }
    
    def get_new_business_budget_capacity(self):
        bank_balance = BusinessBankBalanceSnapshot.objects.filter(business=self).order_by("created_timestamp").last()
        if not bank_balance:
            return {
                "status": "bank balance is unknown"
            }
        else:
            return bank_balance.get_aggregate_balance_data(.5)
    
    def get_budget_capacity(self):
        bank_balance = BusinessBankBalanceSnapshot.objects.filter(business=self).order_by("created_timestamp").last()
        if not bank_balance:
            return {
                "status": "bank balance is unknown"
            }
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
                    "c5.large": 10
                },
                "autoscaling_groups": [
                    {
                        "name": "collaya-inference-asg",
                        "desired_capacity": 2,
                        "max_capacity": 5,
                        "current_capacity": 2
                    }
                ],
                "lambda_invocations_per_minute": {
                    "limit": 1000,
                    "current_usage": 540
                },
                "ecs_clusters": [
                    {
                        "name": "collaya-prod",
                        "running_tasks": 12,
                        "available_capacity_percent": 40
                    }
                ]
            },
            "gpu": {
                "available_gpus": {
                    "g4dn.xlarge": 2,
                    "p3.2xlarge": 0
                },
                "inference_queue_length": 3,
                "expected_wait_time_seconds": 120
            },
            "storage": {
                "s3": {
                    "total_objects": 120000,
                    "total_size_gb": 310.4
                },
                "ebs": {
                    "total_volumes": 12,
                    "used_storage_gb": 240
                },
                "efs": {
                    "used_storage_gb": 38
                }
            },
            "network": {
                "api_gateway": {
                    "requests_per_second": {
                        "limit": 5000,
                        "current_usage": 1300
                    }
                },
                "bandwidth_mbps": {
                    "ingress": 220,
                    "egress": 180
                }
            },
            "cost": {
                "month_to_date_spend_usd": 342.18,
                "forecasted_monthly_spend_usd": 720,
                "budget_limit_usd": 1000
            },
            "alerts": [
                "Low GPU availability in us-west-2",
                "Lambda usage > 50% of quota",
                "S3 nearing 90% of cost allocation warning threshold"
            ]
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
                    f"{g.goal_id} (kpi_id={g.kpi.kpi_id}): {g.status}"
                    for g in goals
                )
            }
        
        return goals_status
    
    def snapshot_code(
            self,
            self_driving_task_iteration: 'SelfDrivingTaskIteration',
            include_erie_common=True
    ):
        instructions = common.get(self_driving_task_iteration, ['evaluation_json', 'instructions'])
        sandbox_path = Path(self_driving_task_iteration.self_driving_task.sandbox_path)
        
        files_to_index = list(common.iterate_files_deep(
            sandbox_path,
            file_extensions=[".py", ".html", ".js", ".css", ".scss", ".yaml", ".sh", ".txt", "Dockerfile"],
            gitignore_patterns=["core/migrations/"]
        ))
        
        if include_erie_common:
            erie_common_path = "venv/lib/python3.11/site-packages/erieiron_public"
            files_to_index += [f"{erie_common_path}/{f}" for f in common.iterate_files_deep(
                sandbox_path / erie_common_path,
                file_extensions=[".py", ".html", ".js", ".css", ".scss", ".yaml", ".sh"],
                respect_git_ignore=False,
                gitignore_patterns=["migrations/"]
            )]
        
        for relative_file_path in common.strings(files_to_index):
            code_file = CodeFile.get(self, relative_file_path)
            version = code_file.get_latest_version()
            
            if not version:
                code_file.init_from_codefile(
                    self_driving_task_iteration,
                    relative_file_path
                )
            else:
                if (sandbox_path / relative_file_path).read_text() != version.code:
                    CodeFile.update_from_path(
                        self_driving_task_iteration,
                        (sandbox_path / relative_file_path),
                        instructions
                    )
    
    def get_secrets_root_key(self, aws_env: AwsEnv):
        from erieiron_common import aws_utils
        project_name = aws_utils.sanitize_aws_name(self.service_token, max_length=64)
        return f"z/{project_name}/{aws_env.value}"


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
    potential_competitors_data = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
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
    required_disclaimers_or_terms = models.JSONField(null=True, encoder=ErieIronJSONEncoder)
    risk_rating = models.TextField(null=True, choices=Level.choices())
    recommended_entity_structure = models.TextField(choices=LlcStructure.choices(), null=False)
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
            "available_balance": common.safe_sum(available_balance) * (1 - reserve_percent),
            "current_balance": common.safe_sum(current_balance) * (1 - reserve_percent),
            "status": common.join_with_and(list(set(status)))
        }


class BusinessBankBalanceSnapshotAccount(BaseErieIronModel):
    snapshot = models.ForeignKey("BusinessBankBalanceSnapshot", on_delete=models.CASCADE)
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
    initiative_type = models.TextField(choices=InitiativeType.choices(), default=InitiativeType.PRODUCT)
    test_file_path = models.TextField(null=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    title = models.TextField()
    description = models.TextField()
    priority = models.TextField(choices=Level.choices())
    linked_kpis = models.ManyToManyField("BusinessKPI", related_name="initiatives", blank=True)
    linked_goals = models.ManyToManyField("BusinessGoal", related_name="initiatives", blank=True)
    expected_kpi_lift = models.JSONField(default=dict)
    requires_unit_tests = models.BooleanField(default=True)
    
    def all_tasks_complete(self) -> bool:
        if self.tasks.count() == 0:
            return False
        
        return not self.tasks.exclude(status=TaskStatus.COMPLETE).exists()
    
    def llm_data(self):
        return {
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "requirements": [{
                "summary": req.summary,
                "acceptance_criteria": req.acceptance_criteria,
                "testable": req.testable
            } for req in self.requirements.order_by()]
        }


class ProductRequirement(BaseErieIronModel):
    id = models.TextField(primary_key=True)  # requirement_token
    initiative = models.ForeignKey(Initiative, on_delete=models.CASCADE, related_name='requirements')
    product_initiative = models.TextField(null=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    summary = models.TextField()
    acceptance_criteria = models.TextField()
    testable = models.BooleanField(default=True)


class Task(BaseErieIronModel):
    id = models.TextField(primary_key=True)
    created_timestamp = models.DateTimeField(auto_now_add=True)
    initiative = models.ForeignKey(Initiative, on_delete=models.CASCADE, related_name="tasks")
    product_initiative = models.TextField(null=True)
    task_type = models.TextField(choices=TaskType.choices(), default=TaskType.CODING_APPLICATION, null=False)
    status = models.TextField(null=False, choices=TaskStatus.choices())
    
    validated_requirements = models.ManyToManyField(ProductRequirement, blank=True, related_name="validation_tasks")
    description = models.TextField()
    depends_on = models.ManyToManyField(
        'self',
        symmetrical=False,
        related_name='dependent_tasks',
        blank=True
    )
    risk_notes = models.TextField()
    test_plan = models.TextField()
    completion_criteria = models.JSONField(default=list)
    comment_requests = models.JSONField(default=list)
    current_spend = models.FloatField(null=True)
    max_budget_usd = models.FloatField(null=True)
    attachments = models.JSONField(default=list)
    created_by = models.TextField(null=True)
    
    input_fields = models.JSONField(default=dict)
    output_fields = models.JSONField(default=list)
    
    requires_test = models.BooleanField(default=True)
    execution_schedule = models.TextField(choices=TaskExecutionSchedule.choices(), default=TaskExecutionSchedule.ONCE)
    execution_start_time = models.DateTimeField(null=True, blank=True)
    timeout_seconds = models.IntegerField(null=True, blank=True)
    guidance = models.TextField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.description} - {self.id}"
    
    def to_dict(self):
        d = self.__dict__
        
        return d
    
    def create_execution(self, input_data=None, iteration=None) -> 'TaskExecution':
        with transaction.atomic():
            return TaskExecution.objects.create(
                task=self,
                iteration=iteration,
                status=TaskStatus.NOT_STARTED,
                input=input_data or {}
            )
    
    def get_last_execution(self) -> Optional['TaskExecution']:
        return self.taskexecution_set.filter(executed_time__isnull=False).order_by("executed_time").last()
    
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
        for t in self.depends_on.filter(status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)
        for t in self.dependent_tasks.filter(status__in=[TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)
    
    def allow_execution(self):
        b = self.initiative.business
        if Business.get_erie_iron_business().id == b.id:
            return True
        
        return BusinessStatus.ACTIVE.eq(self.initiative.business.status)
    
    def create_self_driving_env(self, reset_code_dir=False) -> 'SelfDrivingTask':
        business = self.initiative.business
        
        self_driving_task, created = SelfDrivingTask.objects.get_or_create(
            task_id=self.id,
            defaults={
                "sandbox_path": os.path.abspath(tempfile.TemporaryDirectory().name),
                "main_name": common.safe_filename(self.id),
                "goal": self.get_work_desc(),
                "business": business
            }
        )
        
        if reset_code_dir and not created:
            if Path(self_driving_task.sandbox_path).exists():
                common.delete_dir(self_driving_task.sandbox_path)
            
            SelfDrivingTask.objects.filter(pk=self_driving_task.pk).update(
                sandbox_path=os.path.abspath(tempfile.TemporaryDirectory().name)
            )
            self_driving_task.refresh_from_db(fields=["sandbox_path"])
        
        from erieiron_autonomous_agent.business_level_agents.eng_lead import bootstrap_repo
        git = self_driving_task.get_git()
        try:
            if git.source_exists():
                git.pull()
            else:
                git.clone(business.github_repo_url)
        except Exception as e:
            if "repository not found" in str(e).lower():
                bootstrap_repo(business, git)
                if git.source_exists():
                    git.pull()
                else:
                    git.clone(business.github_repo_url)
            else:
                raise e
        
        return self_driving_task
    
    def get_upstream_outputs(self):
        return {
            task.id: task.get_last_execution().output
            for task in self.depends_on.all()
        }
    
    def get_sub_domain(self) -> str:
        from erieiron_common.aws_utils import sanitize_aws_name
        return sanitize_aws_name([str(self.id)], max_length=63).lower()
    
    def get_domain_and_cert(self, aws_env: AwsEnv) -> tuple[str, str, str]:
        from erieiron_common import aws_utils, domain_manager
        
        business = self.initiative.business
        sub_domain = self.get_sub_domain()
        
        erie_iron_subdomain = f"{sub_domain}.erieironllc.com"
        
        preferred_domain = self.get_preferred_domain()
        
        computed_domain = None
        if AwsEnv.PRODUCTION.eq(aws_env):
            computed_domain = (business.domain or erie_iron_subdomain)
        elif business.domain:
            computed_domain = f"{sub_domain}.{business.domain}"
        else:
            computed_domain = erie_iron_subdomain
        if computed_domain:
            computed_domain = computed_domain.rstrip('.').lower()
        
        candidate_domains = []
        for domain_candidate in [preferred_domain, computed_domain, erie_iron_subdomain.rstrip('.')]:
            if domain_candidate and domain_candidate not in candidate_domains:
                candidate_domains.append(domain_candidate)
        if not candidate_domains:
            candidate_domains.append(erie_iron_subdomain.rstrip('.'))
        
        route53_client = aws_utils.client("route53")
        aws_region = aws_env.get_aws_region()
        fallback_root_domain = "erieironllc.com"
        allowed_zone_roots = {fallback_root_domain}
        if business.domain:
            allowed_zone_roots.add(business.domain.rstrip('.').lower())
        hosted_zone_cache: dict[str, Optional[str]] = {}

        def _zone_supports_domain(zone_id: Optional[str], domain_candidate: str) -> tuple[bool, Optional[str]]:
            if not zone_id:
                return False, None
            try:
                zone_resp = route53_client.get_hosted_zone(Id=zone_id) or {}
            except ClientError as exc:
                logging.warning(
                    "Failed to inspect hosted zone %s for domain %s: %s",
                    zone_id,
                    domain_candidate,
                    exc
                )
                return False, None
            zone_name = (((zone_resp.get("HostedZone") or {})).get("Name") or "").rstrip('.').lower()
            if not zone_name:
                return False, None
            normalized_candidate = domain_candidate.rstrip('.').lower()
            if zone_name not in allowed_zone_roots:
                logging.info(
                    "Hosted zone %s is not an allowed root for %s. Allowed roots: %s",
                    zone_name,
                    normalized_candidate,
                    sorted(allowed_zone_roots)
                )
                return False, zone_name
            if not normalized_candidate.endswith(zone_name):
                logging.info(
                    "Hosted zone %s does not align with domain %s; skipping",
                    zone_name,
                    normalized_candidate
                )
                return False, zone_name
            return True, zone_name

        def _locate_allowed_zone(domain_candidate: str) -> tuple[Optional[str], Optional[str]]:
            normalized_candidate = domain_candidate.rstrip('.').lower()
            for root in allowed_zone_roots:
                if not normalized_candidate.endswith(root):
                    continue
                if root not in hosted_zone_cache:
                    hosted_zone_cache[root] = domain_manager.find_hosted_zone_id(route53_client, root)
                zone_id = hosted_zone_cache.get(root)
                if zone_id:
                    return zone_id, root
            return None, None

        selected_domain = None
        selected_hosted_zone_id = None
        selected_certificate_arn = None
        
        attempted_domain_management = False
        
        for domain_name in candidate_domains:
            for attempt in range(2):
                hosted_zone_id = domain_manager.find_hosted_zone_id(route53_client, domain_name)
                certificate_arn = domain_manager.find_certificate_arn(domain_name, aws_region)

                zone_valid, _ = _zone_supports_domain(hosted_zone_id, domain_name)
                if not zone_valid:
                    hosted_zone_id, _ = _locate_allowed_zone(domain_name)

                if hosted_zone_id and certificate_arn:
                    selected_domain = domain_name.rstrip('.')
                    selected_hosted_zone_id = hosted_zone_id
                    selected_certificate_arn = certificate_arn
                    break

                is_business_domain_candidate = business.domain and domain_name.endswith(business.domain.lower())
                should_attempt_management = (
                        attempt == 0
                        and is_business_domain_candidate
                        and business.needs_domain
                        and not attempted_domain_management
                )
                
                if should_attempt_management:
                    attempted_domain_management = True
                    try:
                        domain_manager.manage_domain(business)
                    except Exception:
                        logging.exception("Failed to manage root domain for %s", business.service_token)
                    continue
                
                if not hosted_zone_id:
                    logging.info(
                        "Unable to locate a Route53 hosted zone for domain '%s'. switching to fallback %s",
                        domain_name,
                        erie_iron_subdomain
                    )
                elif not certificate_arn:
                    logging.info(
                        "Unable to locate an ACM certificate for domain '%s'. switching to fallback %s",
                        domain_name,
                        erie_iron_subdomain
                    )
                break
            
            if selected_domain:
                break
        
        if not selected_domain:
            selected_domain = candidate_domains[-1].rstrip('.')
            if not selected_hosted_zone_id:
                selected_hosted_zone_id, _ = _locate_allowed_zone(selected_domain)
            if not selected_certificate_arn:
                selected_certificate_arn = domain_manager.find_certificate_arn(selected_domain, aws_region)

        if selected_domain and not selected_hosted_zone_id:
            logging.warning(
                "No allowed Route53 hosted zone found for %s within %s",
                selected_domain,
                sorted(allowed_zone_roots)
            )

        if selected_domain and self.get_preferred_domain() != selected_domain:
            self.selfdrivingtask.domain = selected_domain
            self.selfdrivingtask.save(update_fields=["domain"])

        return selected_domain, selected_hosted_zone_id, selected_certificate_arn
    
    def get_preferred_domain(self):
        if self.selfdrivingtask:
            preferred_domain = (self.selfdrivingtask.domain or "").rstrip('.') or None
        else:
            preferred_domain = None
        if preferred_domain:
            preferred_domain = preferred_domain.lower()
        return preferred_domain
    
    def get_name(self):
        root_str = str(self.id)
        
        if "task_" in root_str:
            root_str = root_str[len("task_"):]
        
        root_str = root_str.split("--")[-1]
        
        return (root_str
                .replace("_", " ")
                .replace("-", " ")
                .capitalize())


# Design system and handoff models
class DesignComponent(BaseErieIronModel):
    id = models.TextField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True)


class TaskExecution(BaseErieIronModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    iteration = models.ForeignKey("SelfDrivingTaskIteration", on_delete=models.CASCADE, null=True)
    status = models.TextField(default=TaskStatus.NOT_STARTED, null=False, choices=TaskStatus.choices())
    created_time = models.DateTimeField(auto_now_add=True)
    executed_time = models.DateTimeField(null=True)
    input = models.JSONField(default=dict, null=True)
    output = models.JSONField(default=dict, null=True)
    error_msg = models.TextField(null=True)
    
    def resolve(self, output=None, status=TaskStatus.COMPLETE, error_msg=None):
        with transaction.atomic():
            TaskExecution.objects.filter(id=self.id).update(
                status=status,
                error_msg=error_msg,
                output=output or {},
                executed_time=common.get_now()
            )
        
        self.refresh_from_db()
        return self


class TaskDesignRequirements(BaseErieIronModel):
    task = models.OneToOneField("Task", on_delete=models.CASCADE, related_name="design_handoff")
    component_ids = models.ManyToManyField(DesignComponent, blank=True)
    layout = models.JSONField(default=dict, null=True)
    component_tree = models.JSONField(default=dict, null=True)
    notes = models.TextField(null=True)


class SelfDrivingTask(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    main_name = models.TextField(null=False)
    test_file_path = models.TextField(null=True)
    sandbox_path = models.TextField(null=False)
    domain = models.TextField(null=True)
    cloudformation_stack_name = models.TextField(null=True)
    cloudformation_stack_id = models.TextField(null=True)
    goal = models.TextField(null=False)
    task = models.OneToOneField("Task", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    config_path = models.TextField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def get_git(self) -> GitWrapper:
        return GitWrapper(self.sandbox_path)
    
    def rollback_to(self, iteraton: 'SelfDrivingTaskIteration'):
        # we don't want any files created in future iterations hanging around, so delete everything
        # there's prob a more efficient way to do this, but this is fine for now
        for cf in CodeFile.objects.filter(codeversion__task_iteration__task=self):
            common.quietly_delete(
                common.assert_in_sandbox(
                    self.sandbox_path,
                    cf.file_path
                )
            )
        
        for i in self.selfdrivingtaskiteration_set.order_by("timestamp"):
            i.write_to_disk()
            if i.id == iteraton.id:
                break
    
    def get_best_iteration(self) -> 'SelfDrivingTaskIteration':
        best = self.selfdrivingtaskbestiteration_set.order_by("timestamp").last()
        
        return best.iteration if best else self.get_most_recent_iteration()
    
    def get_most_recent_iteration(self) -> 'SelfDrivingTaskIteration':
        return self.selfdrivingtaskiteration_set.order_by("timestamp").last()
    
    def get_most_recent_code_version(self) -> Optional['CodeVersion']:
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
        ).aggregate(
            total=Sum("price")
        )
        return result["total"] or 0.0
    
    def iterate(self) -> Tuple['SelfDrivingTaskIteration', Optional['SelfDrivingTaskIteration'], Optional['SelfDrivingTaskIteration']]:
        iteration_to_modify = None
        most_recent_iteration = self.get_most_recent_iteration()
        
        try:
            iteration_to_modify = SelfDrivingTaskIteration.objects.get(
                id=most_recent_iteration.evaluation_json.get("iteration_id_to_modify")
            )
        except:
            ...
        
        if not iteration_to_modify:
            iteration_to_modify = self.get_most_recent_iteration()
        
        max_version = SelfDrivingTaskIteration.objects.filter(
            self_driving_task=self
        ).aggregate(
            models.Max("version_number")
        )["version_number__max"] or 0
        
        with transaction.atomic():
            current_iteration = SelfDrivingTaskIteration.objects.create(
                self_driving_task=self,
                version_number=max_version + 1
            )
        
        if not iteration_to_modify:
            iteration_to_modify = current_iteration
        
        SelfDrivingTaskIteration.objects.filter(id=current_iteration.id).update(
            start_iteration=iteration_to_modify if iteration_to_modify.id != current_iteration.id else None
        )
        
        return current_iteration, most_recent_iteration, iteration_to_modify
    
    DEV_STACK_TOKEN_LENGTH = 6

    def _generate_dev_stack_token(self) -> str:
        """Generate a stack token whose leading character is a letter."""
        for _ in range(32):
            token = common.random_string(self.DEV_STACK_TOKEN_LENGTH).lower()
            if token and token[0].isalpha():
                return token
        fallback_suffix = common.random_string(self.DEV_STACK_TOKEN_LENGTH - 1).lower()
        return f"a{fallback_suffix}"

    def get_require_tests(self) -> bool:
        return self.task and self.task.requires_test
    
    def get_cloudformation_key_prefix(self, environment: AwsEnv):
        from erieiron_common.aws_utils import sanitize_aws_name
        task_fragment = self._task_identifier_fragment()

        if AwsEnv.PRODUCTION.eq(environment):
            return sanitize_aws_name(
                [self.business.service_token, task_fragment],
                max_length=40
            )

        stack_name = self.get_cloudformation_stack_name(environment)
        token = self._extract_dev_stack_token(stack_name)
        if token:
            components = [token, task_fragment]
        else:
            components = [stack_name]
        return sanitize_aws_name(components, max_length=40)

    def namespace_domain_with_stack_identifier(self, domain_name: str | None, environment: AwsEnv) -> str | None:
        """Adjust the provided domain so its leading label reflects the current StackIdentifier."""
        if not domain_name:
            return domain_name

        normalized_domain = domain_name.rstrip('.').lower()
        stack_identifier = self.get_cloudformation_key_prefix(environment)
        if not stack_identifier:
            return normalized_domain

        from erieiron_common.aws_utils import sanitize_aws_name
        namespaced_label = sanitize_aws_name(stack_identifier, max_length=63)

        if AwsEnv.PRODUCTION.eq(environment):
            parts = normalized_domain.split('.')
            if parts and parts[0] == namespaced_label:
                return normalized_domain
            return '.'.join([namespaced_label, normalized_domain])

        parts = normalized_domain.split('.')
        if not parts:
            return namespaced_label

        if parts[0] == namespaced_label:
            return normalized_domain

        parts[0] = namespaced_label
        return '.'.join(parts)

    def get_cloudformation_stack_name(self, environment: AwsEnv):
        if AwsEnv.PRODUCTION.eq(environment):
            return self._build_production_stack_name()

        if not AwsEnv.DEV.eq(environment):
            raise ValueError(f"Unsupported AWS environment: {environment}")

        stack_name = self.cloudformation_stack_name
        if stack_name:
            return stack_name

        new_name = self._generate_unique_cloudformation_stack_name(environment)
        with transaction.atomic():
            SelfDrivingTask.objects.filter(id=self.id).update(
                cloudformation_stack_name=new_name
            )
        self.refresh_from_db(fields=["cloudformation_stack_name"])
        return new_name

    def get_sandbox(self) -> Path:
        return Path(self.sandbox_path)

    def rotate_cloudformation_stack_name(
            self,
            environment: AwsEnv,
            *,
            status: str | None = None,
            reason: str | None = None
    ) -> str:
        """Allocate a fresh CloudFormation stack name and tombstone the old one."""
        if not AwsEnv.DEV.eq(environment):
            raise ValueError("Stack name rotation is only supported for DEV environment")

        current_name = self.cloudformation_stack_name
        if current_name:
            try:
                import boto3
                logging.info(f"Deleting tombstoned stack {current_name}")
                cf_client = boto3.client("cloudformation", region_name=environment.get_aws_region())
                cf_client.delete_stack(StackName=current_name)
            except Exception as e:
                logging.exception(e)
            
            AgentTombstone.objects.update_or_create(
                business=self.business,
                name=current_name,
                defaults={
                    "data_json": {
                        "entity": "cloudformation_stack",
                        "environment": environment.value,
                        "name": current_name,
                        "task_id": self.id,
                        "initiative_id": self.task.initiative_id if self.task else None,
                        "reason": reason or f"Stack rotated after entering {status}" if status else "Stack rotated",
                        "status": status,
                        "timestamp": common.get_now().isoformat(),
                    }
                }
            )

        exclude = {current_name} if current_name else set()
        new_name = self._generate_unique_cloudformation_stack_name(environment, exclude=exclude)

        with transaction.atomic():
            SelfDrivingTask.objects.filter(id=self.id).update(
                cloudformation_stack_name=new_name
            )
        self.refresh_from_db(fields=["cloudformation_stack_name"])

        logging.info(
            "Rotated CloudFormation stack name for task %s: %s -> %s", self.id, current_name, new_name
        )
        return new_name

    def _generate_unique_cloudformation_stack_name(
            self,
            environment: AwsEnv,
            *,
            exclude: set[str] | None = None
    ) -> str:
        if not AwsEnv.DEV.eq(environment):
            raise ValueError("Unique stack name generation is only supported for DEV environment")
        exclude = set(filter(None, exclude or set()))
        base_components = self._base_cloudformation_stack_components(environment)
        tombstoned_names = set(
            AgentTombstone.objects.filter(
                business=self.business,
                data_json__entity="cloudformation_stack",
                data_json__environment=environment.value
            ).values_list("name", flat=True)
        )
        exclude.update(filter(None, tombstoned_names))

        # Always avoid the current persisted name
        if self.cloudformation_stack_name:
            exclude.add(self.cloudformation_stack_name)

        for attempt in range(20):
            token = self._generate_dev_stack_token()
            candidate = self._compose_dev_stack_name(token, base_components)
            if candidate not in exclude:
                return candidate
        
        # Fallback: include a timestamp to guarantee uniqueness
        token = self._generate_dev_stack_token()
        fallback_suffix = f"fallback-{int(common.get_now().timestamp())}"
        return self._compose_dev_stack_name(token, [*base_components, fallback_suffix])

    def _base_cloudformation_stack_components(self, environment: AwsEnv) -> list[str]:
        parts = [self.business.service_token]
        if AwsEnv.PRODUCTION.eq(environment):
            parts.append(self._task_identifier_fragment())
        else:
            initiative_id = getattr(self.task, "initiative_id", None)
            parts.extend(
                filter(
                    None,
                    [
                        environment.value,
                        str(initiative_id) if initiative_id is not None else None,
                        self._task_identifier_fragment()
                    ]
                )
            )
        return parts

    def _build_production_stack_name(self) -> str:
        from erieiron_common.aws_utils import sanitize_aws_name
        return sanitize_aws_name(
            self._base_cloudformation_stack_components(AwsEnv.PRODUCTION),
            max_length=128
        )

    def _compose_dev_stack_name(self, token: str, base_components: list[str]) -> str:
        from erieiron_common.aws_utils import sanitize_aws_name
        return sanitize_aws_name([token, *base_components], max_length=128)

    def _extract_dev_stack_token(self, stack_name: str) -> str | None:
        if not stack_name:
            return None
        token = stack_name.split('-', 1)[0]
        if len(token) == self.DEV_STACK_TOKEN_LENGTH and token.isalnum():
            return token
        return None

    def _task_identifier_fragment(self) -> str:
        task_id = getattr(self, "task_id", None)
        if task_id is not None:
            return str(task_id).replace("task_", "")
        return str(getattr(self.task, "id", "task"))


class SelfDrivingTaskIteration(BaseErieIronModel):
    self_driving_task = models.ForeignKey(SelfDrivingTask, on_delete=models.CASCADE, null=True)
    start_iteration = models.ForeignKey('SelfDrivingTaskIteration', on_delete=models.CASCADE, null=True)
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
    planning_json = models.JSONField(null=True)
    evaluation_json = models.JSONField(null=True)
    slowest_cloudformation_resources = models.JSONField(null=True)
    routing_json = models.JSONField(null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def get_latest_execution(self) -> TaskExecution:
        te = self.taskexecution_set.last()
        
        if te:
            return te
        else:
            return self.self_driving_task.task.create_execution(iteration=self)
    
    def get_llm_cost(self) -> Tuple[float, int]:
        totals = self.llmrequest_set.aggregate(
            total_price=Sum('price'),
            total_tokens=Sum('token_count')
        )
        return totals['total_price'] or 0, totals['total_tokens'] or 0
    
    def write_to_disk(self):
        self_driving_task = self.self_driving_task
        
        sandbox_path = self_driving_task.get_sandbox()
        business = self_driving_task.business
        
        for code_file in business.codefile_set.exclude(
                file_path__in=[
                    self_driving_task.test_file_path
                ]
        ).order_by("file_path"):
            if code_file.file_path.startswith("/"):
                logging.error(f"code got indexed with a root path!: {code_file.file_path}")
                continue
            
            if code_file.file_path.startswith(str(os.getcwd())):
                logging.error(f"erie iron code got indexed!: {code_file.file_path}")
                continue
            
            code_version = code_file.get_version(self, default_to_latest=True)
            if code_version:
                code_version.write_to_disk(sandbox_path)
            elif code_file.allow_autonomous_delete():
                common.quietly_delete(sandbox_path / code_file.get_path())
                logging.info(f"{code_file.get_path()} did not exist at iteration {self.id}.  removing from disk")
    
    def get_code_version(self, code_file: 'CodeFile'):
        if isinstance(code_file, Path):
            code_file = CodeFile.get(
                self.self_driving_task.business,
                self.get_relative_path(code_file)
            )
        
        code_version_to_modify = code_file.get_version(self)
        
        if not code_version_to_modify:
            code_version_to_modify = code_file.get_latest_version()
        
        if not code_version_to_modify:
            code_version_to_modify = code_file.init_from_codefile(
                self,
                code_file.file_path
            )
        
        return code_version_to_modify
    
    def get_relative_path(self, code_file):
        try:
            return Path(code_file).relative_to(self.self_driving_task.sandbox_path)
        except:
            return code_file
    
    def get_previous_iteration(self) -> 'SelfDrivingTaskIteration':
        try:
            return self.get_previous_by_timestamp()
        except:
            return None
    
    def has_error(self) -> bool:
        return any([
            "error" in self.evaluation_json,
            "test_errors" in self.evaluation_json
        ])
    
    def get_unit_test_errors(self) -> list[dict]:
        return common.ensure_list(
            common.get(self, ["evaluation_json", "test_errors"])
        )
    
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
            
            return error_info.get("summary"), error_info.get("logs")
        else:
            error_info = common.first(evaluation_json.get("evaluation", []))
            if error_info:
                return error_info.get("summary"), error_info.get("details")
            else:
                return "unknown", "unknown"
    
    def goal_achieved(self):
        return common.parse_bool(common.get(self, ["evaluation_json", "goal_achieved"], False))
    
    def get_all_code_versions(self):
        return {
            cv.code_file_id: cv for cv in self.codeversion_set.all().order_by("created_at")
        }.values()


class SelfDrivingTaskBestIteration(BaseErieIronModel):
    task = models.ForeignKey(SelfDrivingTask, on_delete=models.CASCADE, null=True)
    iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)


class RunningProcess(BaseErieIronModel):
    task_execution = models.OneToOneField(TaskExecution, on_delete=models.CASCADE, related_name="process", null=True, blank=True)
    process_id = models.IntegerField(null=True, blank=True)
    container_id = models.TextField(null=True, blank=True)  # For docker processes
    execution_type = models.TextField(max_length=20, choices=[('local', 'Local'), ('docker', 'Docker')])
    log_file_path = models.TextField(null=True, blank=True)
    log_tail = models.TextField(blank=True, default="")  # Store last ~1000 chars of log
    started_at = models.DateTimeField(auto_now_add=True)
    is_running = models.BooleanField(default=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    
    def update_log_tail(self, max_chars=100000):
        """Update the log_tail field with the latest log content"""
        if self.log_file_path and Path(self.log_file_path).exists():
            try:
                with open(self.log_file_path, 'r') as f:
                    content = f.read()
                    self.log_tail = content[-max_chars:] if len(content) > max_chars else content
                    self.save(update_fields=['log_tail'])
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
        self.save(update_fields=['is_running', 'terminated_at'])
        
        try:
            if self.execution_type == 'docker' and self.container_id:
                # Kill docker container
                subprocess.run(['docker', 'kill', self.container_id], check=True)
            elif self.execution_type == 'local' and self.process_id:
                # Kill local process
                os.kill(self.process_id, signal.SIGTERM)
            
            return True
        except Exception as e:
            logging.warning(f"Failed to kill process {self.id}: {e}")
            return False


class LlmRequest(BaseErieIronModel):
    business = models.ForeignKey(Business, on_delete=models.SET_NULL, null=True)
    initiative = models.ForeignKey(Initiative, on_delete=models.SET_NULL, null=True)
    task_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.SET_NULL, null=True)
    token_count = models.IntegerField()
    chat_millis = models.IntegerField(default=0)
    title = models.TextField(null=True, default="Unknown")
    price = models.FloatField()
    llm_model = models.TextField(null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    input_messages = models.JSONField(null=True)
    response = models.TextField(null=True)


class CodeFile(BaseErieIronModel):
    class Meta:
        unique_together = ['business', 'file_path']
    
    ARCHITECTURE_FILE = "docs/architecture.md"
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    
    # file_path is not the primary key because code file paths change as we refactor
    file_path = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.file_path} - {self.id}"
    
    def allow_autonomous_delete(self) -> bool:
        return self.file_path not in [
            CodeFile.ARCHITECTURE_FILE
        ]
    
    def get_path(self) -> Path:
        return Path(self.file_path)
    
    def get_base_name(self) -> Path:
        return common.get_basename(self.get_path())
    
    def get_dir(self) -> Path:
        return self.get_path().parent
    
    def get_latest_version(self, self_driving_task: SelfDrivingTask = None) -> 'CodeVersion':
        if self_driving_task:
            code_version = self.codeversion_set.filter(
                task_iteration__self_driving_task=self_driving_task
            ).order_by("created_at").last()
            
            return code_version if code_version else self.get_latest_version()
        else:
            return self.codeversion_set.order_by("created_at").last()
    
    def get_version(
            self,
            iteration: SelfDrivingTaskIteration,
            default_to_latest=False
    ) -> Optional['CodeVersion']:
        code_version = self.codeversion_set.filter(
            task_iteration=iteration
        ).order_by("created_at").last()
        
        if code_version:
            return code_version
        
        if default_to_latest:
            return self.get_latest_version()
        
        # Find the code version from the iteration that is closest without being after this iteration
        # This gets the "last known version" of this file at the time of the given iteration
        closest_version = self.codeversion_set.filter(
            task_iteration__self_driving_task=iteration.self_driving_task,
            task_iteration__timestamp__lte=iteration.timestamp
        ).order_by('-task_iteration__timestamp').first()
        
        return closest_version
    
    @staticmethod
    def get(business: Business, relative_path: Path) -> 'CodeFile':
        if str(relative_path).startswith("/"):
            raise Exception(f"CodeFile needs to be a relative path.  got {relative_path}")
        
        return CodeFile.objects.get_or_create(
            business=business,
            file_path=relative_path
        )[0]
    
    @staticmethod
    def init_from_codefile(
            task_iteration: SelfDrivingTaskIteration,
            relative_file_path: Path
    ) -> 'CodeVersion':
        sandbox_path = Path(task_iteration.self_driving_task.sandbox_path)
        file_path = sandbox_path / relative_file_path
        file_path = common.assert_exists(file_path)
        
        if str(file_path).startswith("/"):
            asdf = 1
        
        return CodeFile.update_from_path(
            task_iteration,
            file_path,
            code_instructions=f"initial code from existing file"
        )
    
    def update(
            self,
            task_iteration: SelfDrivingTaskIteration,
            code: str,
            code_instructions=None
    ):
        if code is None:
            asdfasdf = 1
        
        cv = CodeVersion.objects.create(
            task_iteration=task_iteration,
            code_file=self,
            code_instructions=code_instructions,
            code=code
        )
        
        file_path = Path(task_iteration.self_driving_task.sandbox_path) / self.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code)
        
        return cv
    
    @staticmethod
    def update_from_path(
            task_iteration: SelfDrivingTaskIteration,
            abs_file_path: Path,
            code_instructions=None
    ) -> 'CodeVersion':
        abs_file_path = common.assert_exists(abs_file_path)
        
        self_driving_task = task_iteration.self_driving_task
        business = self_driving_task.business
        
        code = abs_file_path.read_text()
        with transaction.atomic():
            relative_path = abs_file_path.relative_to(self_driving_task.sandbox_path)
            code_file = CodeFile.get(business, relative_path)
            
            return code_file.update(
                task_iteration=task_iteration,
                code=code,
                code_instructions=code_instructions
            )
    
    def get_version_for_iteration(self, iteration: SelfDrivingTaskIteration) -> 'CodeVersion':
        return (self.get_version(iteration)
                or self.get_latest_version()
                or self.init_from_codefile(
                    iteration,
                    common.assert_exists(Path(iteration.self_driving_task.sandbox_path) / self.file_path)
                ))


class CodeVersion(BaseErieIronModel):
    code_file = models.ForeignKey(CodeFile, on_delete=models.CASCADE)
    task_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE)
    code_instructions = models.JSONField(null=True)
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    codebert_embedding = VectorField(dimensions=768, null=True)
    
    def has_diff(self) -> bool:
        previous_version = (
            CodeVersion.objects
            .filter(code_file=self.code_file, created_at__lt=self.created_at)
            .order_by('-created_at')
            .first()
        )
        
        if not previous_version:
            return False
    
        return common.is_not_empty(self.get_diff())
    
    def get_diff(self) -> str:
        try:
            previous_version = (
                CodeVersion.objects
                .filter(code_file=self.code_file, created_at__lt=self.created_at)
                .order_by('-created_at')
                .first()
            )
            
            file_name = os.path.basename(self.code_file.get_path())
            
            diff_lines = difflib.unified_diff(
                common.default_str(previous_version.code).splitlines(),
                common.default_str(self.code).splitlines(),
                fromfile=f"old_{file_name}",
                tofile=f"new_{file_name}",
                lineterm=""
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
    
    def update_codebert_embedding(self):
        from erieiron_autonomous_agent.utils.codegen_utils import get_codebert_embedding
        
        path = self.code_file.file_path
        logging.info(f"about to update embedding for {path}")
        
        CodeVersion.objects.filter(id=self.id).update(
            codebert_embedding=get_codebert_embedding(self.code)
        )
        
        self.refresh_from_db(fields=["codebert_embedding"])
        self.codemethod_set.all().delete()
        
        ext = os.path.splitext(path)[1]
        if ext in codegen_utils.LANGUAGES:
            for method_data in extract_methods(ext, self.code):
                CodeMethod.objects.create(
                    code_version=self,
                    name=method_data["name"],
                    parameters=method_data["parameters"],
                    code=method_data["code"],
                    codebert_embedding=get_codebert_embedding(method_data["code"])
                )
    
    def get_llm_message_data(self, include_diff=True) -> dict:
        code_file = self.code_file
        d = {
            "file_path": code_file.file_path,
            "code": self.code,
        }
        
        if include_diff:
            d['diff_against_previous_version'] = self.get_diff()
        
        return d


class CodeMethod(BaseErieIronModel):
    code_version = models.ForeignKey(CodeVersion, on_delete=models.CASCADE)
    name = models.TextField()
    parameters = models.JSONField(default=dict)
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    codebert_embedding = VectorField(dimensions=768, null=True)


@receiver(post_save, sender=CodeVersion)
def update_codebert_embedding_on_save(sender, instance, created, update_fields, **kwargs):
    """
    Automatically update CodeBERT embeddings when a CodeVersion is created or when the code field changes.
    """
    # Always update embeddings for new instances
    if created:
        instance.update_codebert_embedding()
        return
    
    # For existing instances, check if the code field was updated
    if update_fields is None:
        # update_fields is None means all fields were potentially updated
        # In this case, we'll update the embedding to be safe
        instance.update_codebert_embedding()
    elif 'code' in update_fields:
        # The code field was explicitly updated
        instance.update_codebert_embedding()


@receiver(pre_delete, sender=SelfDrivingTaskIteration)
def kill_running_processes_on_iteration_delete(sender, instance, **kwargs):
    """
    Kill any running processes associated with this iteration before deletion.
    """
    running_processes = RunningProcess.objects.filter(
        task_execution__iteration=instance,
        is_running=True
    )
    
    for process in running_processes:
        try:
            process.kill_process()
            logging.info(f"Killed running process {process.id} for iteration {instance.id}")
        except Exception as e:
            logging.warning(f"Failed to kill process {process.id} for iteration {instance.id}: {e}")


class AgentLesson(BaseErieIronModel):
    source_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.SET_NULL, null=True)
    agent_step = models.TextField()
    pattern = models.TextField()
    invalid_lesson = models.BooleanField(default=False, null=True)
    trigger = models.TextField()
    lesson = models.TextField()
    context_tags = models.JSONField(default=list)
    embedding = VectorField(dimensions=384, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    @staticmethod
    def create_from_data(agent_step, data: dict, source_iteration=None) -> 'AgentLesson':
        tag_text = common.safe_join(data.get("context_tags", []), delim=",")
        text = f'Step: {agent_step}. {data.get("pattern_description")}. {data.get("trigger")}. {data.get("lesson")}. Tags: {tag_text}'
        embedding = mini_lm_model.encode(text, normalize_embeddings=True)
        
        lesson = AgentLesson.objects.create(
            source_iteration=source_iteration,
            agent_step=agent_step,
            pattern=data.get("pattern_description"),
            trigger=data.get("trigger"),
            lesson=data.get("lesson"),
            context_tags=data.get("context_tags"),
            embedding=embedding
        )
    
    def get_llm_data(self):
        return {
            "agent_step": self.agent_step,
            "valid_lesson": not self.invalid_lesson,
            "pattern": self.pattern,
            "trigger": self.trigger,
            "lesson": self.lesson,
            "context_tags": self.context_tags
        }
