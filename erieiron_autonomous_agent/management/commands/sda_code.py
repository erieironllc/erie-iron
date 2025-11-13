import os
import textwrap

import boto3
from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent_tofu
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
        task_id = options.get("task_id")
        if not task_id:
            initiative_id = os.getenv("LOCAL_DEV_INITIATIVE", options.get("initiative_id"))
            if not initiative_id:
                raise Exception(f"need a task or initiative id")
            
            task: Task = (
                Initiative.objects.get(id=initiative_id)
                .tasks.exclude(status__in=[TaskStatus.BLOCKED, TaskStatus.COMPLETE])
                .order_by("created_timestamp")
                .first()
            )
            task_id = task.id
        
        print(textwrap.dedent(f"""
        
        SDA for task id {task_id}
        Running as {boto3.client("sts").get_caller_identity()['Arn']}

        """))
        self_driving_coder_agent_tofu.execute(task_id, SdaInitialAction.valid_or(options.get("action")))
