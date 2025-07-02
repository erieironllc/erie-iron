import json
import uuid
from collections import defaultdict

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from erieiron_common import common
from erieiron_common.enums import TaskStatus, PubSubMessageType, BusinessIdeaSource, Constants
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import Task, Initiative, Business, SelfDrivingTask, SelfDrivingTaskIteration, TaskExecution, RunningProcess
from erieiron_ui.view_utils import send_response, redirect, rget


def hello(request):
    return HttpResponse("hello world")


def view_businesses(request):
    erieiron_business = Business.get_erie_iron_business()

    # Get all running processes for the businesses page
    all_running_processes = RunningProcess.objects.filter(is_running=True).order_by('-started_at')

    return send_response(
        request, "businesses.html", {
            "erieiron_business": erieiron_business,
            "businesses": Business.objects.exclude(id=erieiron_business.id).order_by("created_at"),
            "all_running_processes": all_running_processes
        },
        breadcrumbs=[
            (reverse(view_businesses), erieiron_business.name)
        ]
    )


def view_business(request, business_id):
    business = get_object_or_404(Business, pk=business_id)

    # business.businessceodirective_set
    tasks = Task.objects.filter(initiative__business=business)

    return send_response(
        request,
        "business.html", {
            "tasks": tasks,
            "business": business
        },
        breadcrumbs=[
            (reverse(view_businesses), Business.get_erie_iron_business().name)
        ]
    )


def view_initiative(request, initiative_id):
    initiative = get_object_or_404(Initiative, pk=initiative_id)
    business = initiative.business

    tasks = list(initiative.tasks.all())

    sdt_dict = {
        sdt.id: sdt
        for sdt in SelfDrivingTask.objects.filter(related_task__in=tasks).order_by("created_at")
    }

    task_sdti_dict = defaultdict(list)
    for sdti in SelfDrivingTaskIteration.objects.filter(task__related_task__in=tasks).order_by("timestamp"):
        sdt: SelfDrivingTask = sdt_dict.get(sdti.task_id)
        task_sdti_dict[sdt.related_task_id].append(sdti)

    task_execution_dict = defaultdict(list)
    for te in TaskExecution.objects.filter(task__in=tasks).order_by("executed_time"):
        task_execution_dict[te.task_id].append(te)

    task_type_tasks = defaultdict(list)
    for task in tasks:
        # Add iteration and execution data to each task
        first_iteration = common.first(task_sdti_dict[task.id])
        last_execution = common.last(task_execution_dict[task.id])
        last_execution_time = None
        if last_execution:
            last_execution_time = last_execution.executed_time

        if not last_execution_time:
            last_iteration = common.last(task_sdti_dict[task.id])
            if last_iteration:
                last_execution_time = last_iteration.timestamp
            else:
                last_execution_time = None

        task.first_iteration_time = first_iteration.timestamp if first_iteration else None
        task.last_execution_time = last_execution_time

        task_type_tasks[TaskStatus(task.status)].append(task)

    task_datas = []
    for status in TaskStatus.get_sorted_status():
        for task in task_type_tasks[status]:
            task_datas.append(task)

    return send_response(
        request, "initiative.html",
        {
            "tasks": task_datas,
            "initiative": initiative
        },
        breadcrumbs=[
            (f"{reverse(view_business, args=[business.id])}#product-initiatives", business.name)
        ]
    )


def view_task(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    initiative = task.initiative
    business = initiative.business

    # Add iteration and execution data for related tasks
    def add_task_metadata(t):
        self_driving_task = SelfDrivingTask.objects.filter(related_task_id=t.id).first()
        if self_driving_task:
            first_iteration = self_driving_task.selfdrivingtaskiteration_set.order_by("timestamp").first()
            t.first_iteration_time = first_iteration.timestamp if first_iteration else None
        else:
            t.first_iteration_time = None

        last_execution = t.taskexecution_set.order_by("-executed_time").first()
        t.last_execution_time = last_execution.executed_time if last_execution else None
        return t

    # Add metadata to dependent tasks
    for dependent_task in task.depends_on.all():
        add_task_metadata(dependent_task)

    for blocking_task in task.dependent_tasks.all():
        add_task_metadata(blocking_task)

    self_driving_task = SelfDrivingTask.objects.filter(related_task_id=task_id).first()
    if self_driving_task:
        iterations = self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp")
    else:
        iterations = []

    task_executions = list(task.taskexecution_set.order_by("-executed_time"))

    running_processes = list(RunningProcess.objects.filter(
        iteration__task__related_task=task,
        is_running=True
    ))

    return send_response(
        request, "task.html",
        {
            "task_executions": task_executions,
            "iterations": iterations,
            "self_driving_task": self_driving_task,
            "task": task,
            "running_processes": running_processes
        },
        breadcrumbs=[
            (f"{reverse(view_business, args=[business.id])}#product-initiatives", business.name),
            (f"{reverse(view_initiative, args=[initiative.id])}#tasks", initiative.title)
        ]
    )


def view_self_driver_iteration(request, iteration_id):
    iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)

    task = iteration.task.related_task
    initiative = task.initiative
    business = initiative.business

    total_price, total_tokens = iteration.get_llm_cost()
    
    # Get running processes for this specific iteration
    running_processes = RunningProcess.objects.filter(
        iteration=iteration,
        is_running=True
    )

    return send_response(
        request, "iteration.html",
        {
            "task": task,
            "initiative": initiative,
            "business": business,

            "total_price": total_price,
            "total_tokens": total_tokens,
            "iteration": iteration,
            "running_processes": running_processes
        },
        breadcrumbs=[
            (f"{reverse(view_business, args=[business.id])}#product-initiatives", business.name),
            (f"{reverse(view_initiative, args=[initiative.id])}#tasks", initiative.title),
            (f"{reverse(view_task, args=[task.id])}#iterations", task.id)
        ]
    )


def action_resolve_task(request, task_id):
    task = get_object_or_404(Task, pk=task_id)

    task.create_execution().resolve(
        json.loads(rget(request, "output"))
    )
    Task.objects.filter(id=task_id).update(
        status=TaskStatus.COMPLETE
    )
    PubSubManager.publish_id(
        PubSubMessageType.TASK_COMPLETED,
        task_id
    )

    return redirect(reverse('view_task', args=[task_id]))


def action_retry_task(request, task_id):
    task = get_object_or_404(Task, pk=task_id)

    Task.objects.filter(id=task_id).update(
        status=TaskStatus.NOT_STARTED
    )
    PubSubManager.publish_id(
        PubSubMessageType.TASK_UPDATED,
        task_id
    )

    return redirect(reverse('view_task', args=[task_id]))


def action_restart_task(request, task_id):
    task = get_object_or_404(Task, pk=task_id)

    # Reset task status and clear any existing executions
    Task.objects.filter(id=task_id).update(
        status=TaskStatus.NOT_STARTED
    )

    PubSubManager.publish_id(
        PubSubMessageType.TASK_UPDATED,
        task_id
    )

    messages.success(request, f'Task {task_id} restarted successfully!')
    return redirect(reverse('view_task', args=[task_id]))


def action_delete_task(request, task_id):
    if request.method != 'POST':
        raise Exception()

    try:
        task = get_object_or_404(Task, pk=task_id)
        initiative_id = task.initiative.id

        task.update_dependent_tasks()
        task.delete()

        messages.success(request, f'Task {task_id} deleted successfully!')
        return redirect(reverse('view_initiative', args=[initiative_id]))
    except Task.DoesNotExist:
        messages.error(request, 'Task not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error deleting task: {str(e)}')
        return redirect(reverse('view_businesses'))


def action_add_business(request):
    if request.method != 'POST':
        raise Exception()

    business_name = rget(request, 'business_name', '').strip()
    business_description = rget(request, 'business_description', '').strip()

    if not business_name:
        messages.error(request, 'Business name is required.')
        return redirect(reverse('view_businesses'))

    if not business_description:
        messages.error(request, 'Business description is required.')
        return redirect(reverse('view_businesses'))

    business = Business.objects.create(
        name=business_name,
        source=BusinessIdeaSource.HUMAN,
        raw_idea=business_description
    )

    PubSubManager.publish(
        PubSubMessageType.BUSINESS_IDEA_SUBMITTED,
        payload={
            'existing_business_id': business.id,
            'source': BusinessIdeaSource.HUMAN,
            'idea_content': business_description
        }
    )

    messages.success(request, 'Business idea submitted successfully! It will be reviewed and processed.')
    return redirect(reverse('view_businesses'))


def action_delete_business(request, business_id):
    if request.method != 'POST':
        raise Exception()

    try:
        business = get_object_or_404(Business, id=business_id)
        business_name = business.name
        business.delete()
        messages.success(request, f'Business "{business_name}" deleted successfully!')
    except Business.DoesNotExist:
        messages.error(request, 'Business not found.')
    except Exception as e:
        messages.error(request, f'Error deleting business: {str(e)}')

    return redirect(reverse('view_businesses'))


def action_add_initiative(request):
    if request.method != 'POST':
        raise Exception()

    title = rget(request, 'title', '').strip()
    initiative_type = rget(request, 'initiative_type', '').strip()
    description = rget(request, 'description', '').strip()
    requires_unit_tests = request.POST.get('requires_unit_tests') == 'on'

    if not title:
        messages.error(request, 'Initiative title is required.')
        return redirect(reverse('view_businesses'))

    if not initiative_type:
        messages.error(request, 'Initiative type is required.')
        return redirect(reverse('view_businesses'))

    if not description:
        messages.error(request, 'Initiative description is required.')
        return redirect(reverse('view_businesses'))

    erieiron_business = Business.get_erie_iron_business()

    initiative = Initiative.objects.create(
        id=str(uuid.uuid4()),
        business=erieiron_business,
        title=title,
        requires_unit_tests=requires_unit_tests,
        initiative_type=initiative_type,
        description=description
    )

    PubSubManager.publish_id(
        PubSubMessageType.INITIATIVE_DEFINITION_REQUESTED,
        initiative.id
    )

    messages.success(request, 'Initiative created successfully!')
    return redirect(f"{reverse('view_businesses')}#initiatives")


def action_dowork_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()

    try:
        initiative = get_object_or_404(Initiative, id=initiative_id)
        initiative_title = initiative.title

        for t in initiative.tasks.exclude(status=TaskStatus.COMPLETE):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)

        messages.success(request, f'Work successfully kicked off on "{initiative_title}"')
    except Initiative.DoesNotExist:
        messages.error(request, 'Initiative not found.')
    except Exception as e:
        messages.error(request, f'Error with initiative: {str(e)}')

    return redirect(f"{reverse('view_businesses')}#initiatives")


def action_delete_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()

    try:
        initiative = get_object_or_404(Initiative, id=initiative_id)
        initiative_title = initiative.title
        initiative.delete()
        messages.success(request, f'Initiative "{initiative_title}" deleted successfully!')
    except Initiative.DoesNotExist:
        messages.error(request, 'Initiative not found.')
    except Exception as e:
        messages.error(request, f'Error deleting initiative: {str(e)}')

    return redirect(f"{reverse('view_businesses')}#initiatives")


def action_find_business(request):
    business_name = rget(request, 'business_name', '').strip()
    business_description = rget(request, 'business_description', '').strip()

    business = Business.objects.create(
        name=f"{Constants.NEW_BUSINESS_NAME_PREFIX} {common.get_now()}",
        source=BusinessIdeaSource.BUSINESS_FINDER_AGENT
    )

    PubSubManager.publish(
        PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
        payload={
            "placehold_business_id": business.id
        }
    )

    return redirect(reverse('view_businesses'))


def action_update_task_guidance(request, task_id):
    if request.method != 'POST':
        raise Exception()

    try:
        task = get_object_or_404(Task, pk=task_id)
        guidance = rget(request, 'guidance', '').strip()

        Task.objects.filter(id=task_id).update(guidance=guidance)

        messages.success(request, 'Task guidance updated successfully!')
        return redirect(reverse('view_task', args=[task_id]) + '#guidance')
    except Task.DoesNotExist:
        messages.error(request, 'Task not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error updating guidance: {str(e)}')
        return redirect(reverse('view_task', args=[task_id]))


def action_kill_process(request, process_id):
    if request.method != 'POST':
        raise Exception()

    try:
        running_process = get_object_or_404(RunningProcess, pk=process_id)

        # Determine redirect target based on whether process has an iteration or TaskExecution
        if running_process.iteration:
            # Redirect back to the iteration page
            redirect_url = reverse('view_self_driver_iteration', args=[running_process.iteration.id]) + '#processes'
        elif common.get(running_process, ["iteration", "task", "related_task_id"]):
            task_id = running_process.iteration.task.related_task_id
            redirect_url = reverse('view_task', args=[task_id]) + '#processes'
        else:
            redirect_url = reverse('view_businesses')

        if running_process.kill_process():
            messages.success(request, f'Process {process_id} killed successfully!')
        else:
            messages.warning(request, f'Failed to kill process {process_id} - it may have already terminated.')

        return redirect(redirect_url)
    except RunningProcess.DoesNotExist:
        messages.error(request, 'Process not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error killing process: {str(e)}')
        return redirect(reverse('view_businesses'))


def action_delete_iteration(request, iteration_id):
    if request.method != 'POST':
        raise Exception()

    try:
        iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)
        task_id = iteration.task.related_task.id

        iteration.delete()

        messages.success(request, f'Iteration {iteration_id} deleted successfully!')
        return redirect(reverse('view_task', args=[task_id]) + '#iterations')
    except SelfDrivingTaskIteration.DoesNotExist:
        messages.error(request, 'Iteration not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error deleting iteration: {str(e)}')
        return redirect(reverse('view_businesses'))
