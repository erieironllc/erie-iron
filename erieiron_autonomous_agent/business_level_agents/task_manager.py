import logging

from django.db import transaction
from django.db.models import F, Value, Q
from django.db.models.functions import Coalesce

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import Task
from erieiron_common import aws_utils, settings_common
from erieiron_common.enums import PubSubMessageType, TaskType
from erieiron_common.message_queue.pubsub_manager import PubSubManager

TASKTYPE_TO_MSGTYPE = {
    TaskType.HUMAN_WORK: PubSubMessageType.HUMAN_WORK_REQUESTED,
    TaskType.DESIGN_WEB_APPLICATION: PubSubMessageType.DESIGN_WORK_REQUESTED,
    TaskType.CODING_ML: PubSubMessageType.CODING_WORK_REQUESTED,
    TaskType.INITIATIVE_VERIFICATION: PubSubMessageType.CODING_WORK_REQUESTED,
    TaskType.CODING_APPLICATION: PubSubMessageType.CODING_WORK_REQUESTED,
    TaskType.TASK_EXECUTION: PubSubMessageType.CODING_WORK_REQUESTED
}


def on_task_updated(task_id):
    task = Task.objects.get(id=task_id)

    status = TaskStatus(task.status)
    if status in [TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]:
        if not task.are_dependencies_complete():
            # we are blocked!
            Task.objects.filter(id=task_id).update(
                status=TaskStatus.BLOCKED
            )
        elif task.allow_execution():
            # not started, not blocked.  do it to it!
            Task.objects.filter(id=task_id).update(
                status=TaskStatus.IN_PROGRESS
            )

            msg_type = TASKTYPE_TO_MSGTYPE.get(
                TaskType(task.task_type)
            )

            PubSubManager.publish_id(msg_type, task.id)
    elif TaskStatus.FAILED.eq(status):
        logging.error(f"Task {task.id}: {task.description} FAILED")
    elif TaskStatus.COMPLETE.eq(status):
        logging.info(f"Task {task.id}: {task.description} is complete")


def on_task_complete(task_id):
    task = Task.objects.get(id=task_id)

    Task.objects.filter(id=task_id).update(
        status=TaskStatus.COMPLETE
    )
    task.update_dependent_tasks()
    
    initiative = task.initiative
    if initiative and initiative.all_tasks_complete():
        PubSubManager.publish_id(
            PubSubMessageType.INITIATIVE_DEPLOY_REQUESTED, 
            initiative.id
        )


def on_task_spend(payload):
    task_id = payload['task_id']
    usd_spent = float(payload['usd_spent'])

    with transaction.atomic():
        Task.objects.filter(id=task_id).update(
            current_spend=Coalesce(F('current_spend'), Value(0)) + usd_spent
        )


def on_task_failed(payload):
    if isinstance(payload, str):
        payload = {
            "task_id": payload,
            "error": "unknown"
        }

    task = Task.objects.get(id=payload.get("task_id"))

    cc_parts = "<br>".join([cc for cc in task.completion_criteria])

    err = payload.get('error', '').replace("\n", "<br>").replace("\t", "&nbsp;&nbsp;")
    logging.error(f"""
Task {task.id}: {task.description} FAILED
{err} """)

    aws_utils.get_aws_interface().send_email(
        subject=f"Task failed: {task.id} - {task.description}",
        recipient="jj@jjschultz.com",
        body=f"""
<h3>TaskID</h3>{task.id}<hr>

{settings_common.BASE_URL}/task/task_build_dev_runtime_container

<h3>Error</h3><pre>{err}</pre><hr>

<h3>Completion Criteria</h3>{cc_parts}
"""
    )

    Task.objects.filter(id=task.id).update(
        status=TaskStatus.FAILED
    )

    task.update_dependent_tasks()

    return task.id
