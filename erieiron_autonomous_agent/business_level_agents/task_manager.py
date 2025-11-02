import logging
from typing import Optional

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce

import settings
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import Task
from erieiron_common import aws_utils
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

SERIALIZED_TASK_TYPES = {
    TaskType.PRODUCTION_DEPLOYMENT,
    TaskType.INITIATIVE_VERIFICATION,
    TaskType.CODING_APPLICATION,
    TaskType.CODING_ML,
    TaskType.DESIGN_WEB_APPLICATION,
}

SERIALIZED_TASK_TYPE_VALUES = {task_type.value for task_type in SERIALIZED_TASK_TYPES}
SERIALIZED_READY_STATUS_VALUES = {
    TaskStatus.NOT_STARTED.value,
    TaskStatus.BLOCKED.value,
}


def _get_task_type(task: Task) -> Optional[TaskType]:
    try:
        return TaskType(task.task_type)
    except ValueError:
        return None


def _find_next_ready_serial_task(task: Task) -> Optional[Task]:
    if not task.initiative_id:
        return None
    
    candidates = (
        Task.objects
        .filter(
            initiative=task.initiative,
            task_type__in=SERIALIZED_TASK_TYPE_VALUES,
            status__in=SERIALIZED_READY_STATUS_VALUES,
        )
        .order_by("created_timestamp", "id")
    )
    
    for candidate in candidates:
        if candidate.are_dependencies_complete():
            return candidate
    
    return None


def _has_serial_task_in_progress(task: Task) -> bool:
    if not task.initiative_id:
        return False
    
    return Task.objects.filter(
        initiative=task.initiative,
        task_type__in=SERIALIZED_TASK_TYPE_VALUES,
        status=TaskStatus.IN_PROGRESS.value
    ).exclude(id=task.id).exists()


def _should_execute_task(task: Task) -> bool:
    task_type = _get_task_type(task)
    if task_type not in SERIALIZED_TASK_TYPES:
        return True
    
    if _has_serial_task_in_progress(task):
        return False
    
    next_ready = _find_next_ready_serial_task(task)
    if next_ready is None:
        return True
    
    return next_ready.id == task.id


def _maybe_publish_next_serial_task(task: Task) -> None:
    task_type = _get_task_type(task)
    if task_type not in SERIALIZED_TASK_TYPES:
        return
    
    if _has_serial_task_in_progress(task):
        return
    
    next_ready = _find_next_ready_serial_task(task)
    if next_ready and next_ready.id != task.id:
        PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, next_ready.id)


def _trigger_next_serial_task(task: Task) -> None:
    task_type = _get_task_type(task)
    if task_type not in SERIALIZED_TASK_TYPES:
        return
    
    if _has_serial_task_in_progress(task):
        return
    
    next_ready = _find_next_ready_serial_task(task)
    if next_ready:
        PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, next_ready.id)


def on_task_updated(task_id):
    task = Task.objects.get(id=task_id)
    
    status = TaskStatus(task.status)
    if status in [TaskStatus.NOT_STARTED, TaskStatus.BLOCKED]:
        if not task.are_dependencies_complete():
            # we are blocked!
            Task.objects.filter(id=task_id).update(
                status=TaskStatus.BLOCKED
            )
        elif task.allow_execution() and _should_execute_task(task):
            # not started, not blocked.  do it to it!
            Task.objects.filter(id=task_id).update(
                status=TaskStatus.IN_PROGRESS
            )
            
            msg_type = TASKTYPE_TO_MSGTYPE.get(
                TaskType(task.task_type)
            )
            
            PubSubManager.publish_id(msg_type, task.id)
        else:
            _maybe_publish_next_serial_task(task)
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
    
    _trigger_next_serial_task(task)
    
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
        recipient="erieironllc@gmail.com",
        body=f"""
<h3>TaskID</h3>{task.id}<hr>

{settings.BASE_URL}/task/task_build_dev_runtime_container

<h3>Error</h3><pre>{err}</pre><hr>

<h3>Completion Criteria</h3>{cc_parts}
"""
    )
    
    Task.objects.filter(id=task.id).update(
        status=TaskStatus.FAILED
    )
    
    task.update_dependent_tasks()
    
    return task.id


def on_initiative_green_lit(initiative_id):
    """Handle INITIATIVE_GREEN_LIT event by triggering task execution for all tasks in the initiative."""
    from erieiron_autonomous_agent.models import Initiative
    
    try:
        initiative = Initiative.objects.get(id=initiative_id)
        
        # Get all tasks for this initiative that are ready to be executed
        tasks = initiative.tasks.filter(
            status__in=[TaskStatus.NOT_STARTED.value, TaskStatus.BLOCKED.value]
        )
        
        # Trigger task updates for all tasks in the initiative
        for task in tasks:
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, task.id)
        
        logging.info(f"Initiative {initiative_id} green lit - triggered {tasks.count()} tasks for execution")
    
    except Initiative.DoesNotExist:
        logging.error(f"Initiative {initiative_id} not found when handling INITIATIVE_GREEN_LIT")
    except Exception as e:
        logging.error(f"Error handling INITIATIVE_GREEN_LIT for initiative {initiative_id}: {str(e)}")
