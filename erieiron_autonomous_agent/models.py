import difflib
import logging
import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Tuple, Optional

from django.db import models, transaction
from django.db.models import Sum
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver
from pgvector.django import VectorField

from erieiron_autonomous_agent.enums import BusinessStatus, BusinessGuidanceRating, TrafficLight, TaskStatus
from erieiron_autonomous_agent.utils import codegen_utils
from erieiron_autonomous_agent.utils.codegen_utils import extract_methods
from erieiron_common import common
from erieiron_common.enums import Level, LlcStructure, TaskExecutionSchedule, InitiativeType, GoalStatus, BusinessIdeaSource, TaskType, AwsEnv
from erieiron_common.git_utils import GitWrapper
from erieiron_common.json_encoder import ErieIronJSONEncoder
from erieiron_common.models import BaseErieIronModel


class Business(BaseErieIronModel):
    name = models.TextField(unique=True)
    source = models.TextField(null=False, choices=BusinessIdeaSource.choices())
    status = models.TextField(default=BusinessStatus.IDEA, choices=BusinessStatus.choices())
    
    service_token = models.TextField(null=True)
    summary = models.TextField(null=True)
    raw_idea = models.TextField(null=True)
    bank_account_id = models.TextField(null=True)
    business_plan = models.TextField(null=True)
    value_prop = models.TextField(null=True)
    revenue_model = models.TextField(null=True)
    audience = models.TextField(null=True)
    core_functions = models.JSONField(default=list)
    execution_dependencies = models.JSONField(default=list)
    growth_channels = models.JSONField(default=list)
    personalization_options = models.JSONField(default=list)
    allow_autonomous_shutdown = models.BooleanField(default=True)
    autonomy_level = models.TextField(null=True, choices=Level.choices())
    github_repo_url = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def get_iam_role_name(self):
        return f"erieiron-{self.service_token}-role"
    
    def get_latest_board_guidance(self):
        return BusinessGuidance.objects.filter(business=self).order_by("created_timestamp").last()
    
    def get_latest_capacity(self):
        return BusinessCapacityAnalysis.objects.filter(business=self).order_by("created_timestamp").last()
    
    def get_latest_analysist(self):
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
    
    def snapshot_code(self, self_driving_task_iteration: 'SelfDrivingTaskIteration', include_erie_common=True):
        instructions = common.get(self_driving_task_iteration, ['evaluation_json', 'instructions'])
        sandbox_path = Path(self_driving_task_iteration.self_driving_task.sandbox_path)
        
        files_to_index = [sandbox_path / f for f in common.iterate_files_deep(
            sandbox_path,
            file_extensions=[".py", ".html", ".js", ".css", ".scss", ".yaml", ".sh", ".txt", "Dockerfile"],
            gitignore_patterns=["core/migrations/"]
        )]
        
        if include_erie_common:
            erie_common_path = sandbox_path / "venv/lib/python3.11/site-packages/erieiron_common"
            files_to_index += [erie_common_path / f for f in common.iterate_files_deep(
                erie_common_path,
                file_extensions=[".py", ".html", ".js", ".css", ".scss", ".yaml", ".sh"],
                respect_git_ignore=False,
                gitignore_patterns=["migrations/"]
            )]
        
        for file_path in files_to_index:
            try:
                relative_file_path = Path(file_path).relative_to(sandbox_path)
            except:
                relative_file_path = file_path
            
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
    initiative_type = models.TextField(choices=InitiativeType.choices(), default=InitiativeType.PRODUCT)
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
    
    def create_self_driving_env(self) -> 'SelfDrivingTask':
        business = self.initiative.business
        
        temp_dir = tempfile.TemporaryDirectory()
        
        self_driving_task, _ = SelfDrivingTask.objects.get_or_create(
            task_id=self.id,
            defaults={
                "sandbox_path": os.path.abspath(temp_dir.name),
                "main_name": common.safe_filename(self.id),
                "goal": self.get_work_desc(),
                "business": business
            }
        )
        
        git = self_driving_task.get_git()
        
        if git.source_exists():
            git.pull()
        else:
            git.clone(business.github_repo_url)
        
        return self_driving_task
    
    def get_upstream_outputs(self):
        return {
            task.id: task.get_last_execution().output
            for task in self.depends_on.all()
        }


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
    sandbox_path = models.TextField(null=False)
    cloudformation_stack_name = models.TextField(null=True)
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
            return last_iteration.log_content
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
        try:
            most_recent_iteration = self.get_most_recent_iteration()
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
        
        return current_iteration, most_recent_iteration, iteration_to_modify
    
    def get_require_tests(self) -> bool:
        return self.task and self.task.requires_test
    
    def get_cloudformation_key_prefix(self, environment: AwsEnv):
        from erieiron_common.aws_utils import sanitize_aws_name
        return sanitize_aws_name(self.get_cloudformation_stack_name(environment), max_length=40)
    
    def get_cloudformation_stack_name(self, environment: AwsEnv):
        from erieiron_common.aws_utils import sanitize_aws_name
        
        cloudformation_stack_name = [
            self.business.service_token,
            environment.value
        ]
        
        if AwsEnv.PRODUCTION.DEV.eq(environment):
            cloudformation_stack_name = [
                *cloudformation_stack_name,
                self.task.initiative.id,
                self.id
            ]
        
        cloudformation_stack_name = sanitize_aws_name(cloudformation_stack_name, max_length=128)
        
        if AwsEnv.PRODUCTION.DEV.eq(environment) and self.cloudformation_stack_name != cloudformation_stack_name:
            with transaction.atomic():
                SelfDrivingTask.objects.filter(id=self.id).update(
                    cloudformation_stack_name=cloudformation_stack_name
                )
            self.refresh_from_db(fields=["cloudformation_stack_name"])
        
        return cloudformation_stack_name
    
    def get_sandbox(self) -> Path:
        return Path(self.sandbox_path)


class SelfDrivingTaskIteration(BaseErieIronModel):
    self_driving_task = models.ForeignKey(SelfDrivingTask, on_delete=models.CASCADE, null=True)
    achieved_goal = models.BooleanField(null=False, default=False)
    version_number = models.IntegerField(null=False, default=0)
    test_module = models.TextField(null=True)
    execute_module = models.TextField(null=True)
    planning_model = models.TextField()
    coding_model = models.TextField()
    log_content = models.TextField()
    evaluation_json = models.JSONField(null=True)
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
        sandbox_path = self.self_driving_task.get_sandbox()
        
        business = self.self_driving_task.business
        for code_file in list(business.codefile_set.all().order_by("file_path")):
            code_version = code_file.get_version(self)
            if code_version:
                code_version.write_to_disk(sandbox_path)
            else:
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
    
    def deployment_failed(self):
        return common.parse_bool(common.get(self, ["evaluation_json", "deployment_failed"], False))
    
    def goal_achieved(self):
        return common.parse_bool(common.get(self, ["evaluation_json", "goal_achieved"], False))


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
    task_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE, null=True)
    token_count = models.IntegerField()
    price = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)


class CodeFile(BaseErieIronModel):
    class Meta:
        unique_together = ['business', 'file_path']
    
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    
    # file_path is not the primary key because code file paths change as we refactor
    file_path = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.file_path} - {self.id}"
    
    def get_path(self) -> Path:
        return Path(self.file_path)
    
    def get_base_name(self) -> Path:
        return common.get_basename(self.get_path())
    
    def get_dir(self) -> Path:
        return self.get_path().parent
    
    def get_latest_version(self) -> 'CodeVersion':
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
    def get(business: Business, code_file_path: Path) -> 'CodeFile':
        return CodeFile.objects.get_or_create(
            business=business,
            file_path=code_file_path
        )[0]
    
    @staticmethod
    def init_from_codefile(
            task_iteration: SelfDrivingTaskIteration,
            relative_file_path: Path
    ) -> 'CodeVersion':
        sandbox_path = Path(task_iteration.self_driving_task.sandbox_path)
        file_path = sandbox_path / relative_file_path
        file_path = common.assert_exists(file_path)
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
            file_path: Path,
            code_instructions=None
    ) -> 'CodeVersion':
        self_driving_task = task_iteration.self_driving_task
        business = self_driving_task.business
        
        code = file_path.read_text()
        
        with transaction.atomic():
            try:
                relative_path = file_path.relative_to(self_driving_task.sandbox_path)
            except:
                relative_path = file_path
            
            code_file = CodeFile.get(business, relative_path)
            return code_file.update(
                task_iteration=task_iteration,
                code=code,
                code_instructions=code_instructions
            )


class CodeVersion(BaseErieIronModel):
    code_file = models.ForeignKey(CodeFile, on_delete=models.CASCADE)
    task_iteration = models.ForeignKey(SelfDrivingTaskIteration, on_delete=models.CASCADE)
    code_instructions = models.JSONField(null=True)
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    codebert_embedding = VectorField(dimensions=768, null=True)
    
    def get_diff(self) -> str:
        try:
            previous_version = self.get_previous_by_created_at()
            diff_lines = difflib.unified_diff(
                common.default_str(previous_version.code).splitlines(),
                common.default_str(self.code).splitlines(),
                fromfile="old.py",
                tofile="new.py",
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
    
    def get_llm_message(self):
        from erieiron_common.llm_apis.llm_interface import LlmMessage
        code_file = self.code_file
        return LlmMessage.user({
            "file_path": code_file.file_path,
            "modified_in_previous_iteration": False,
            "may_edit": "venv/" not in str(code_file.get_path()),
            "code": self.code,
        })


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
