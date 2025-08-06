import json
import uuid
from collections import defaultdict
from datetime import datetime

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from erieiron_autonomous_agent.enums import TaskStatus, BusinessStatus
from erieiron_autonomous_agent.models import Business, LlmRequest, AgentLesson
from erieiron_autonomous_agent.models import Task, Initiative, SelfDrivingTask, SelfDrivingTaskIteration, TaskExecution, RunningProcess
from erieiron_common import common
from erieiron_common.enums import PubSubMessageType, BusinessIdeaSource, Constants, TaskExecutionSchedule, TaskType, Level
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.view_utils import send_response, redirect, rget


def hello(request):
    return HttpResponse("hello world")


def view_businesses(request):
    erieiron_business = Business.get_erie_iron_business()

    # Get all running processes for the businesses page
    all_running_processes = RunningProcess.objects.filter(is_running=True).order_by('-started_at')

    return send_response(
        request, "businesses.html", {
            "erieiron_business": erieiron_business,
            "agent_lessons": AgentLesson.objects.all().order_by("-timestamp"), #("agent_step", "invalid_lesson", "pattern"),
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
            "business": business,
            "business_status_choices": BusinessStatus.choices(),
            "business_source_choices": BusinessIdeaSource.choices(),
            "autonomy_level_choices": Level.choices()
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
        for sdt in SelfDrivingTask.objects.filter(task__in=tasks).order_by("created_at")
    }

    task_sdti_dict = defaultdict(list)
    for sdti in SelfDrivingTaskIteration.objects.filter(self_driving_task__task=tasks).order_by("timestamp"):
        sdt: SelfDrivingTask = sdt_dict.get(sdti.self_driving_task_id)
        task_sdti_dict[sdt.task_id].append(sdti)

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

    # Get all running processes for tasks in this initiative
    running_processes = list(RunningProcess.objects.filter(
        task_execution__task__initiative=initiative
    ).order_by('-started_at'))
    running_processes_count = RunningProcess.objects.filter(task_execution__task__initiative=initiative, is_running=True).count()

    return send_response(
        request, "initiative.html",
        {
            "tasks": task_datas,
            "initiative": initiative,
            "running_processes_count": running_processes_count,
            "running_processes": running_processes
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
        self_driving_task = SelfDrivingTask.objects.filter(task_id=t.id).first()
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

    self_driving_task = SelfDrivingTask.objects.filter(task_id=task_id).first()
    if self_driving_task:
        iterations = self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp")
    else:
        iterations = []
    
    dict_iteration_llmsrequests = defaultdict(list)
    for llm_request in LlmRequest.objects.filter(task_iteration__self_driving_task=self_driving_task):
        dict_iteration_llmsrequests[llm_request.task_iteration_id].append(llm_request)
    
    llm_cost_total = 0
    for iteration in sorted(iterations, key=lambda i:i.timestamp):
        iteration_price = 0
        for llm_request in dict_iteration_llmsrequests[iteration.id]:
            llm_cost_total += llm_request.price
            iteration_price += llm_request.price
        iteration.price = iteration_price
        iteration.total_price = llm_cost_total

    task_executions = list(task.taskexecution_set.order_by("-executed_time"))

    running_processes = list(RunningProcess.objects.filter(
        task_execution__task=task
    ))
    running_processes_count = RunningProcess.objects.filter(
        task_execution__task=task,
        is_running=True
    ).count()

    return send_response(
        request, "task.html",
        {
            "task_executions": task_executions,
            "iterations": iterations,
            "self_driving_task": self_driving_task,
            "task": task,
            "running_processes": running_processes,
            "running_processes_count": running_processes_count,
            "task_status_choices": TaskStatus.choices(),
            "task_execution_schedule_choices": TaskExecutionSchedule.choices(),
            "task_type_choices": TaskType.choices()
        },
        breadcrumbs=[
            (f"{reverse(view_business, args=[business.id])}#product-initiatives", business.name),
            (f"{reverse(view_initiative, args=[initiative.id])}#tasks", initiative.title)
        ]
    )


def view_self_driver_iteration(request, iteration_id):
    iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)

    task = iteration.self_driving_task.task
    initiative = task.initiative
    business = initiative.business

    total_price, total_tokens = iteration.get_llm_cost()

    # Get running processes for this specific iteration
    running_processes = RunningProcess.objects.filter(
        task_execution__iteration=iteration
    ).order_by("-started_at")

    running_processes_count = RunningProcess.objects.filter(task_execution__iteration=iteration, is_running=True).count()

    previous_evaluations = []

    try:
        previous_iteration: SelfDrivingTaskIteration = iteration.get_previous_by_timestamp()
        test = previous_iteration.self_driving_task.task.initiative_id
        previous_evaluations = previous_iteration.evaluation_json
        if isinstance(previous_evaluations, dict):
            previous_evaluations = previous_evaluations.get("evaluation")
    except:
        previous_iteration = None

    try:
        next_iteration: SelfDrivingTaskIteration = iteration.get_next_by_timestamp()
        test = next_iteration.self_driving_task.task.initiative_id
    except:
        next_iteration = None

    return send_response(
        request, 
        "iteration.html",
        {
            "iteration": iteration,
            "previous_iteration": previous_iteration,
            "next_iteration": next_iteration,
            "previous_evaluations": previous_evaluations,
            "running_processes_count": running_processes_count,

            "task": task,
            "initiative": initiative,
            "business": business,

            "total_price": total_price,
            "total_tokens": total_tokens,
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

    try:
        resolve_data = json.loads(rget(request, "output"))
    except:
        resolve_data = {
            'resolve_data': rget(request, "output")
        }

    task.create_execution().resolve(
        resolve_data
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
    return redirect(reverse('view_business', args=[business.id]))


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


def action_update_task(request, task_id):
    if request.method != 'POST':
        raise Exception()

    try:
        task = get_object_or_404(Task, pk=task_id)

        # Get form data
        description = rget(request, 'description', '').strip()
        risk_notes = rget(request, 'risk_notes', '').strip()
        test_plan = rget(request, 'test_plan', '').strip()
        timeout_seconds = rget(request, 'timeout_seconds', '').strip()
        max_budget_usd = rget(request, 'max_budget_usd', '').strip()
        status = rget(request, 'status', '').strip()
        task_type = rget(request, 'task_type', '').strip()
        execution_schedule = rget(request, 'execution_schedule', '').strip()
        execution_start_time = rget(request, 'execution_start_time', '').strip()
        requires_test = request.POST.get('requires_test') == 'on'

        # Prepare update data
        update_data = {
            'description': description,
            'risk_notes': risk_notes,
            'test_plan': test_plan,
            'status': status,
            'task_type': task_type,
            'execution_schedule': execution_schedule,
            'requires_test': requires_test
        }

        # Handle optional fields
        if timeout_seconds:
            try:
                # noinspection PyTypedDict
                update_data['timeout_seconds'] = int(timeout_seconds or 0)
            except ValueError:
                messages.error(request, 'Invalid timeout value.')
                return redirect(reverse('view_task', args=[task_id]) + '#edit')
        else:
            update_data['timeout_seconds'] = None

        if max_budget_usd:
            try:
                # noinspection PyTypedDict
                update_data['max_budget_usd'] = float(max_budget_usd)
            except ValueError:
                messages.error(request, 'Invalid budget value.')
                return redirect(reverse('view_task', args=[task_id]) + '#edit')
        else:
            update_data['max_budget_usd'] = None

        if task_type:
            update_data['task_type'] = task_type
        else:
            update_data['task_type'] = None

        if execution_start_time:
            try:
                # noinspection PyTypedDict
                update_data['execution_start_time'] = datetime.fromisoformat(execution_start_time.replace('T', ' '))
            except ValueError:
                messages.error(request, 'Invalid execution start time format.')
                return redirect(reverse('view_task', args=[task_id]) + '#edit')
        else:
            update_data['execution_start_time'] = None

        # Update the task
        Task.objects.filter(id=task_id).update(**update_data)

        messages.success(request, 'Task updated successfully!')
        return redirect(reverse('view_task', args=[task_id]) + '#edit')
    except Task.DoesNotExist:
        messages.error(request, 'Task not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error updating task: {str(e)}')
        return redirect(reverse('view_task', args=[task_id]))


def action_update_business(request, business_id):
    if request.method != 'POST':
        raise Exception()

    try:
        business = get_object_or_404(Business, pk=business_id)

        # Get form data
        name = rget(request, 'name', '').strip()
        summary = rget(request, 'summary', '').strip()
        raw_idea = rget(request, 'raw_idea', '').strip()
        value_prop = rget(request, 'value_prop', '').strip()
        revenue_model = rget(request, 'revenue_model', '').strip()
        audience = rget(request, 'audience', '').strip()
        status = rget(request, 'status', '').strip()
        source = rget(request, 'source', '').strip()
        autonomy_level = rget(request, 'autonomy_level', '').strip()
        service_token = rget(request, 'service_token', '').strip()
        bank_account_id = rget(request, 'bank_account_id', '').strip()
        github_repo_url = rget(request, 'github_repo_url', '').strip()
        business_plan = rget(request, 'business_plan', '').strip()
        allow_autonomous_shutdown = request.POST.get('allow_autonomous_shutdown') == 'on'

        # Prepare update data
        update_data = {
            'name': name,
            'summary': summary or None,
            'raw_idea': raw_idea or None,
            'value_prop': value_prop or None,
            'revenue_model': revenue_model or None,
            'audience': audience or None,
            'status': status,
            'source': source,
            'service_token': service_token or None,
            'bank_account_id': bank_account_id or None,
            'github_repo_url': github_repo_url or None,
            'business_plan': business_plan or None,
            'allow_autonomous_shutdown': allow_autonomous_shutdown
        }

        # Handle optional autonomy_level
        if autonomy_level:
            update_data['autonomy_level'] = autonomy_level
        else:
            update_data['autonomy_level'] = None

        # Update the business
        Business.objects.filter(id=business_id).update(**update_data)

        messages.success(request, 'Business updated successfully!')
        return redirect(reverse('view_business', args=[business_id]) + '#edit')
    except Business.DoesNotExist:
        messages.error(request, 'Business not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error updating business: {str(e)}')
        return redirect(reverse('view_business', args=[business_id]))


def action_bootstrap_business(request, business_id):
    if request.method != 'POST':
        raise Exception()

    try:
        business = get_object_or_404(Business, pk=business_id)

        PubSubManager.publish_id(
            PubSubMessageType.BUSINESS_BOOTSTRAP_REQUESTED,
            business_id
        )

        messages.success(request, f'Business "{business.name}" has been bootstrapped successfully!')
        return redirect(reverse('view_business', args=[business_id]) + '#edit')
    except Business.DoesNotExist:
        messages.error(request, 'Business not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error bootstrapping business: {str(e)}')
        return redirect(reverse('view_business', args=[business_id]))


def action_kill_process(request, process_id):
    if request.method != 'POST':
        raise Exception()

    try:
        running_process = get_object_or_404(RunningProcess, pk=process_id)

        # Determine redirect target based on whether process has an iteration or TaskExecution
        if running_process.task_execution.iteration:
            # Redirect back to the iteration page
            redirect_url = reverse('view_self_driver_iteration', args=[running_process.task_execution.iteration_id]) + '#processes'
        elif running_process.task_execution.task:
            task_id = running_process.task_execution.task.id
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
        task_id = iteration.self_driving_task.task.id

        iteration.delete()

        messages.success(request, f'Iteration {iteration_id} deleted successfully!')
        return redirect(reverse('view_task', args=[task_id]) + '#iterations')
    except SelfDrivingTaskIteration.DoesNotExist:
        messages.error(request, 'Iteration not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error deleting iteration: {str(e)}')
        return redirect(reverse('view_businesses'))


def action_toggle_lesson_validity(request, lesson_id):
    if request.method != 'POST':
        raise Exception()
    
    try:
        lesson = get_object_or_404(AgentLesson, pk=lesson_id)
        
        # Toggle the invalid_lesson value
        lesson.invalid_lesson = not lesson.invalid_lesson
        lesson.save()
        
        status = "invalid" if lesson.invalid_lesson else "valid" 
        messages.success(request, f'Lesson marked as {status}!')
        return redirect(reverse('view_businesses') + '#lessons')
    except AgentLesson.DoesNotExist:
        messages.error(request, 'Lesson not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error updating lesson: {str(e)}')
        return redirect(reverse('view_businesses'))
