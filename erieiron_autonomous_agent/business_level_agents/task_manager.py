import logging

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce

from erieiron_common.enums import TaskAssigneeType, PubSubMessageType, TaskStatus
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import Task

ASSIGNEE_TO_MSGTYPE = {
    TaskAssigneeType.DESIGN: PubSubMessageType.DESIGN_WORK_REQUESTED,
    TaskAssigneeType.ENGINEERING: PubSubMessageType.CODING_WORK_REQUESTED,
    TaskAssigneeType.HUMAN: PubSubMessageType.HUMAN_WORK_REQUESTED,
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
        else:
            # not started, not blocked.  do it to it!
            Task.objects.filter(id=task_id).update(
                status=TaskStatus.IN_PROGRESS
            )

            msg_type = ASSIGNEE_TO_MSGTYPE.get(
                TaskAssigneeType(task.role_assignee)
            )

            PubSubManager.publish_id(msg_type, task.id)
    elif TaskStatus.FAILED.eq(status):
        logging.error(f"Task {task.id}: {task.task_description} FAILED")
    elif TaskStatus.COMPLETE.eq(status):
        logging.info(f"Task {task.id}: {task.task_description} is complete")
    else:
        raise ValueError(f"un-supported task status {status}")


def on_task_complete(task_id):
    task = Task.objects.get(id=task_id)

    Task.objects.filter(id=task_id).update(
        status=TaskStatus.COMPLETE
    )


def on_task_spend(payload):
    task_id = payload['task_id']
    usd_spent = float(payload['usd_spent'])

    with transaction.atomic():
        Task.objects.filter(id=task_id).update(
            current_spend=Coalesce(F('current_spend'), Value(0)) + usd_spent
        )


def on_task_failed(payload):
    task = Task.objects.get(id=payload.get("task_id"))

    logging.error(f"""
Task {task.id}: {task.task_description} FAILED
{payload.get('error')} """)

    Task.objects.filter(id=task.id).update(
        status=TaskStatus.FAILED
    )

    return task.id
