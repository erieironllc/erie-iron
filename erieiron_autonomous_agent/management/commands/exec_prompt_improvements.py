from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from erieiron_autonomous_agent.models import Task
from erieiron_common import common
from erieiron_common.enums import (
    TaskExecutionSchedule,
    TaskImplementationSourceKind,
    TaskPromptImprovementStatus,
    TaskPromptImprovementTrigger,
)


class Command(BaseCommand):
    help = "Generate scheduled prompt-improvement candidates for due prompt-backed tasks."

    def handle(self, *args, **options):
        now = common.get_now()
        scheduled_tasks = (
            Task.objects.filter(
                prompt_improvement_schedule__in=[
                    TaskExecutionSchedule.HOURLY.value,
                    TaskExecutionSchedule.DAILY.value,
                    TaskExecutionSchedule.WEEKLY.value,
                ],
                implementation_source_kind=TaskImplementationSourceKind.LLM_PROMPT.value,
            )
            .select_related("initiative", "active_implementation_version")
            .annotate(
                completed_execution_count=Count(
                    "taskexecution",
                    filter=Q(taskexecution__executed_time__isnull=False),
                    distinct=True,
                ),
                pending_prompt_improvement_count=Count(
                    "prompt_improvements",
                    filter=Q(
                        prompt_improvements__status=TaskPromptImprovementStatus.PENDING_REVIEW.value
                    ),
                    distinct=True,
                ),
            )
            .order_by("id")
        )

        due_tasks = []
        schedule_deltas = {
            TaskExecutionSchedule.HOURLY.value: 3600,
            TaskExecutionSchedule.DAILY.value: 86400,
            TaskExecutionSchedule.WEEKLY.value: 604800,
        }
        for task in scheduled_tasks:
            if task.pending_prompt_improvement_count:
                continue
            if task.last_prompt_improvement_at is None:
                if task.completed_execution_count >= 3:
                    due_tasks.append(task)
                continue

            elapsed_seconds = (now - task.last_prompt_improvement_at).total_seconds()
            if elapsed_seconds >= schedule_deltas[task.prompt_improvement_schedule]:
                due_tasks.append(task)

        self.stdout.write(f"Found {len(due_tasks)} tasks due for prompt improvement")
        for task in due_tasks:
            improvement = task.generate_prompt_improvement_candidate(
                trigger_source=TaskPromptImprovementTrigger.SCHEDULED
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Generated prompt improvement {improvement.id} for task {task.id}"
                )
            )
