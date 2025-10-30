import textwrap

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.coding_agents.agent_dispatch import (
    get_self_driving_coder_agent_module,
)
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import SdaInitialAction
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import Initiative, Task


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--task_id',
            type=str,
            required=False
        )
        
        parser.add_argument(
            '--initiative_id',
            type=str,
            required=False
        )
        
        parser.add_argument(
            '--action',
            type=str,
            choices=[a.name for a in SdaInitialAction],
            required=False
        )
    
    def handle(self, *args, **options):
        if options.get("initiative_id"):
            task: Task = (
                Initiative.objects.get(id=options.get("initiative_id"))
                .tasks.exclude(status__in=[TaskStatus.BLOCKED, TaskStatus.COMPLETE])
                .order_by("created_timestamp")
                .first()
            )
            task_id = task.id
        else:
            task_id = options.get("task_id")
        
        print(textwrap.dedent(f"""
        
        SDA for task id {task_id}

        """))
        agent = get_self_driving_coder_agent_module()
        agent.execute(task_id, SdaInitialAction.valid_or(options.get("action")))
