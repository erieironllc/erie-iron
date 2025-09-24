import difflib
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote

from django.contrib import messages
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import escape

from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.coding_agents.self_driving_coder_agent import on_reset_task_test
from erieiron_autonomous_agent.enums import TaskStatus, BusinessStatus
from erieiron_autonomous_agent.models import Business, LlmRequest, AgentLesson, CodeFile
from erieiron_autonomous_agent.models import Task, Initiative, SelfDrivingTask, SelfDrivingTaskIteration, TaskExecution, RunningProcess
from erieiron_autonomous_agent.system_agent_llm_interface import get_sys_prompt
from erieiron_common import common
from erieiron_common.enums import PubSubMessageType, BusinessIdeaSource, Constants, TaskExecutionSchedule, TaskType, Level, LlmModel, LlmVerbosity, LlmReasoningEffort, Role
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.view_utils import send_response, redirect, rget, rget_bool, json_endpoint


def hello(request):
    return HttpResponse("hello world")


def view_businesses(request):
    erieiron_business = Business.get_erie_iron_business()
    
    # Get all running processes for the businesses page
    all_running_processes = RunningProcess.objects.filter(is_running=True).order_by('-started_at')
    
    return send_response(
        request, "businesses.html", {
            "erieiron_business": erieiron_business,
            "agent_lessons": AgentLesson.objects.all().order_by("-timestamp"),  # ("agent_step", "invalid_lesson", "pattern"),
            "businesses": Business.objects.exclude(id=erieiron_business.id).order_by("created_at"),
            "all_running_processes": all_running_processes
        },
        breadcrumbs=[
            (reverse(view_businesses), erieiron_business.name)
        ]
    )


def _tab_available_overview(business: Business) -> bool:
    return True


def _tab_context_overview(business: Business) -> dict:
    return {}


def _tab_available_business_plan(business: Business) -> bool:
    return bool(business.business_plan or business.businesskpi_set.exists())


def _tab_context_business_plan(business: Business) -> dict:
    return {
        "business_kpis": business.businesskpi_set.all().order_by("name")
    }


def _tab_available_business_analysis(business: Business) -> bool:
    return business.businessanalysis_set.exists()


def _tab_context_business_analysis(business: Business) -> dict:
    return {
        "business_analysis_list": business.businessanalysis_set.all().order_by("-created_timestamp")
    }


def _tab_available_legal_analysis(business: Business) -> bool:
    return business.businesslegalanalysis_set.exists()


def _tab_context_legal_analysis(business: Business) -> dict:
    return {
        "business_legal_analysis_list": business.businesslegalanalysis_set.all().order_by("-created_timestamp")
    }


def _tab_available_capacity_analysis(business: Business) -> bool:
    return business.businesscapacityanalysis_set.exists()


def _tab_context_capacity_analysis(business: Business) -> dict:
    return {
        "business_capacity_analysis_list": business.businesscapacityanalysis_set.all().order_by("-created_timestamp")
    }


def _tab_available_architecture(business: Business) -> bool:
    return bool(business.architecture)


def _tab_context_architecture(business: Business) -> dict:
    return {}


def _tab_available_product_initiatives(business: Business) -> bool:
    return business.initiative_set.exists()


def _tab_context_product_initiatives(business: Business) -> dict:
    return {
        "initiatives": business.initiative_set.all().order_by("created_timestamp")
    }


def _tab_available_board_guidance(business: Business) -> bool:
    return business.businessguidance_set.exists()


def _tab_context_board_guidance(business: Business) -> dict:
    return {
        "business_guidance_list": business.businessguidance_set.all().order_by("-created_timestamp")
    }


def _tab_available_ceo_guidance(business: Business) -> bool:
    return business.businessceodirective_set.exists()


def _tab_context_ceo_guidance(business: Business) -> dict:
    return {
        "business_ceo_directives": business.businessceodirective_set.all().order_by("-created_timestamp")
    }


def _tab_available_llmrequests(business: Business) -> bool:
    return business.llmrequest_set.exists()


def _tab_context_llmrequests(business: Business) -> dict:
    return {
        "llm_requests": business.llmrequest_set.order_by("-timestamp")
    }


def _tab_available_tasks(business: Business) -> bool:
    return Task.objects.filter(initiative__business=business).exists()


def _tab_context_tasks(business: Business) -> dict:
    return {
        "tasks": Task.objects.filter(initiative__business=business).order_by("created_timestamp")
    }


def _tab_available_edit(business: Business) -> bool:
    return True


def _tab_context_edit(business: Business) -> dict:
    return {
        "business_status_choices": BusinessStatus.choices(),
        "business_source_choices": BusinessIdeaSource.choices(),
        "autonomy_level_choices": Level.choices()
    }


def _build_business_tabs(business: Business) -> list[dict]:
    tabs = []
    for definition in BUSINESS_TAB_DEFINITIONS:
        slug = definition["slug"]
        available = definition["availability_fn"](business)
        if slug == "overview":
            url = reverse('view_business', args=[business.id])
        else:
            url = reverse('view_business_tab', args=[slug, business.id])
        tabs.append({
            "slug": slug,
            "label": definition["label"],
            "url": url,
            "available": available,
        })
    return tabs


def view_business(request, business_id, tab='overview'):
    business = get_object_or_404(Business, pk=business_id)
    tab = (tab or 'overview').lower()
    
    if tab not in BUSINESS_TAB_MAP:
        raise Http404
    
    tabs = _build_business_tabs(business)
    tab_definition = BUSINESS_TAB_MAP[tab]
    
    is_available = next((t for t in tabs if t['slug'] == tab), None)
    if not is_available or not is_available['available']:
        raise Http404
    
    context = {
        "business": business,
        "tabs": tabs,
        "active_tab": tab,
        "tab_template": tab_definition["template"],
    }
    context.update(tab_definition["context_fn"](business))
    
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name),
        (reverse('view_business', args=[business.id]), business.name)
    ]
    if tab != 'overview':
        breadcrumbs.append((reverse('view_business_tab', args=[tab, business.id]), tab_definition["label"]))
    
    return send_response(
        request,
        "business/base.html",
        context,
        breadcrumbs=breadcrumbs
    )


def view_initiative(request, initiative_id):
    initiative = get_object_or_404(Initiative, pk=initiative_id)
    business = initiative.business
    
    tasks = list(initiative.tasks.order_by("created_timestamp"))
    
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
    
    task_datas = tasks
    # for status in TaskStatus.get_sorted_status():
    #     for task in task_type_tasks[status]:
    #         task_datas.append(task)
    
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
            "llm_requests": initiative.llmrequest_set.order_by("-timestamp"),
            "running_processes_count": running_processes_count,
            "running_processes": running_processes
        },
        breadcrumbs=[
            (reverse(view_businesses), Business.get_erie_iron_business().name),
            (reverse(view_business, args=[business.id]), business.name),
            (reverse(view_initiative, args=[initiative.id]), initiative.title)
        ]
    )


def _annotate_task_metadata(task_obj):
    self_driving_task = SelfDrivingTask.objects.filter(task_id=task_obj.id).first()
    if self_driving_task:
        first_iteration = self_driving_task.selfdrivingtaskiteration_set.order_by("timestamp").first()
        task_obj.first_iteration_time = first_iteration.timestamp if first_iteration else None
    else:
        task_obj.first_iteration_time = None
    
    last_execution = task_obj.taskexecution_set.order_by("-executed_time").first()
    task_obj.last_execution_time = last_execution.executed_time if last_execution else None
    return task_obj


def _task_tab_available_overview(task, business, self_driving_task) -> bool:
    return True


def _task_tab_context_overview(task, business, self_driving_task) -> dict:
    return {}


def _task_tab_available_blocked_by(task, business, self_driving_task) -> bool:
    return task.depends_on.exists()


def _task_tab_context_blocked_by(task, business, self_driving_task) -> dict:
    blocked_tasks = [_annotate_task_metadata(t) for t in task.depends_on.all()]
    return {"blocked_tasks": blocked_tasks}


def _task_tab_available_blocks(task, business, self_driving_task) -> bool:
    return task.dependent_tasks.exists()


def _task_tab_context_blocks(task, business, self_driving_task) -> dict:
    blocking_tasks = [_annotate_task_metadata(t) for t in task.dependent_tasks.all()]
    return {"blocking_tasks": blocking_tasks}


def _task_tab_available_iterations(task, business, self_driving_task) -> bool:
    return bool(self_driving_task and self_driving_task.selfdrivingtaskiteration_set.exists())


def _task_tab_context_latest_iteration(task, business, self_driving_task) -> dict:
    iteration = self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp").first()
    
    return {"iteration": iterations}


def _task_tab_context_iterations(task, business, self_driving_task) -> dict:
    if not self_driving_task:
        return {"iterations": []}
    
    iterations = list(self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp"))
    if not iterations:
        return {"iterations": []}
    
    dict_iteration_llmsrequests = defaultdict(list)
    for llm_request in LlmRequest.objects.filter(task_iteration__self_driving_task=self_driving_task).order_by("timestamp"):
        dict_iteration_llmsrequests[llm_request.task_iteration_id].append(llm_request)
    
    llm_cost_total = 0
    for iteration in sorted(iterations, key=lambda i: i.timestamp):
        iteration_price = sum(request.price for request in dict_iteration_llmsrequests.get(iteration.id, []))
        llm_cost_total += iteration_price
        iteration.price = iteration_price
        iteration.total_price = llm_cost_total
    
    return {"iterations": iterations}


def _task_tab_available_executions(task, business, self_driving_task) -> bool:
    return task.taskexecution_set.exists()


def _task_tab_context_executions(task, business, self_driving_task) -> dict:
    task_executions = list(task.taskexecution_set.order_by("-executed_time"))
    return {"task_executions": task_executions}


def _task_tab_available_processes(task, business, self_driving_task) -> bool:
    return RunningProcess.objects.filter(task_execution__task=task).exists()


def _task_tab_context_processes(task, business, self_driving_task) -> dict:
    running_processes = list(RunningProcess.objects.filter(task_execution__task=task).order_by('-started_at'))
    return {"running_processes": running_processes}


def _task_tab_available_llmrequests(task, business, self_driving_task) -> bool:
    return LlmRequest.objects.filter(task_iteration__self_driving_task__task=task).exists()


def _task_tab_context_llmrequests(task, business, self_driving_task) -> dict:
    llm_requests = list(LlmRequest.objects.filter(task_iteration__self_driving_task__task=task).order_by("-timestamp"))
    return {"llm_requests": llm_requests}


def _task_tab_available_guidance(task, business, self_driving_task) -> bool:
    return True


def _task_tab_context_guidance(task, business, self_driving_task) -> dict:
    return {}


def _task_tab_available_testcode(task, business, self_driving_task) -> bool:
    return bool(self_driving_task and self_driving_task.test_file_path)


def _task_tab_context_testcode(task, business, self_driving_task) -> dict:
    test_code_version = None
    if self_driving_task and self_driving_task.test_file_path:
        try:
            test_code_version = CodeFile.get(business, self_driving_task.test_file_path).get_latest_version()
        except Exception as exc:  # pragma: no cover - logging intended
            logging.exception(exc)
    
    return {"test_code_version": test_code_version}


def _task_tab_available_resolve(task, business, self_driving_task) -> bool:
    role_assignee = getattr(task, "role_assignee", None)
    return role_assignee == "HUMAN" and task.status != TaskStatus.COMPLETE.value


def _task_tab_context_resolve(task, business, self_driving_task) -> dict:
    return {}


def _task_tab_available_edit(task, business, self_driving_task) -> bool:
    return True


def _task_tab_context_edit(task, business, self_driving_task) -> dict:
    sandbox_path = self_driving_task.sandbox_path if self_driving_task else ""
    
    cloudformation_stack_name = None
    cloudformation_stack_url = None
    if self_driving_task and self_driving_task.cloudformation_stack_id:
        cloudformation_stack_name = self_driving_task.cloudformation_stack_name or self_driving_task.cloudformation_stack_id
        encoded_stack_id = quote(self_driving_task.cloudformation_stack_id, safe="")
        cloudformation_stack_url = f"https://console.aws.amazon.com/cloudformation/home#/stacks/stackinfo?stackId={encoded_stack_id}"
    
    return {
        "sandbox_path": sandbox_path,
        "cloudformation_stack_name": cloudformation_stack_name,
        "cloudformation_stack_url": cloudformation_stack_url,
        "task_status_choices": TaskStatus.choices(),
        "task_execution_schedule_choices": TaskExecutionSchedule.choices(),
        "task_type_choices": TaskType.choices(),
        "task_role_choices": Role.choices() if hasattr(Role, 'choices') else [],
        "task_phase_choices": [],
    }


def _build_task_tabs(task, business, self_driving_task):
    tabs = []
    for definition in TASK_TAB_DEFINITIONS:
        if definition.get("is_divider"):
            tabs.append(definition)
        else:
            slug = definition["slug"]
            available = definition["availability_fn"](task, business, self_driving_task)
            if slug == "overview":
                url = reverse('view_task', args=[task.id])
            else:
                url = reverse('view_task_tab', args=[slug, task.id])
                
            tab_data = {
                **definition,
                "url": url,
                "available": available,
            }
            
            if slug == "overview":
                tab_data['label'] = task.get_name()
            
            tabs.append(tab_data)
    return tabs


def view_task(request, task_id, tab='overview'):
    task = get_object_or_404(Task, pk=task_id)
    initiative = task.initiative
    business = initiative.business
    
    tab_slug = (tab or 'overview').lower()
    if tab_slug not in TASK_TAB_MAP:
        raise Http404
    
    self_driving_task = SelfDrivingTask.objects.filter(task_id=task.id).first()
    tabs = _build_task_tabs(task, business, self_driving_task)
    tab_definition = TASK_TAB_MAP[tab_slug]
    
    tab_entry = next((t for t in tabs if t['slug'] == tab_slug), None)
    if not tab_entry or not tab_entry['available']:
        raise Http404
    
    context = {
        "task": task,
        "initiative": initiative,
        "business": business,
        "self_driving_task": self_driving_task,
        "task_display_name": task.get_name(),
        "tabs": tabs,
        "active_tab": tab_slug,
        "tab_template": tab_definition["template"],
    }
    context.update(tab_definition["context_fn"](task, business, self_driving_task))
    
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name),
        (reverse('view_business', args=[business.id]), business.name),
        (reverse(view_initiative, args=[initiative.id]), initiative.title),
        (reverse('view_task', args=[task.id]), task.get_name()),
    ]
    if tab_slug != 'overview':
        breadcrumbs.append((reverse('view_task_tab', args=[tab_slug, task.id]), tab_definition["label"]))
    
    return send_response(
        request,
        "task/task_base.html",
        context,
        breadcrumbs=breadcrumbs
    )


def view_self_driver_latest_iteration(request, task_id):
    task: Task = get_object_or_404(Task, pk=task_id)
    try:
        iteration_id = task.selfdrivingtask.get_most_recent_iteration().id
        return view_self_driver_iteration(
            request,
            iteration_id
        )
    except:
        raise Http404(
            "No latest iteration"
        )


def view_self_driver_iteration(request, iteration_id):
    iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)
    
    self_driving_task = iteration.self_driving_task
    task = self_driving_task.task
    initiative = task.initiative
    business = initiative.business
    
    total_price, total_tokens = iteration.get_llm_cost()
    
    # Get running processes for this specific iteration
    running_processes = RunningProcess.objects.filter(
        task_execution__iteration=iteration
    ).order_by("-started_at")
    
    running_processes_count = RunningProcess.objects.filter(task_execution__iteration=iteration, is_running=True).count()
    
    previous_evaluations = []
    
    _, iteration_to_modify = iteration.get_relevant_iterations()
    previous_iteration: SelfDrivingTaskIteration = iteration.get_previous_iteration()
    next_iteration: SelfDrivingTaskIteration = iteration.get_next_iteration()
    
    try:
        last_iteration: SelfDrivingTaskIteration = self_driving_task.get_most_recent_iteration()
    except:
        last_iteration = None
    
    return send_response(
        request,
        "iteration.html",
        {
            "iteration": iteration,
            "previous_iteration": previous_iteration,
            "iteration_to_modify": iteration_to_modify,
            "next_iteration": next_iteration,
            "last_iteration": last_iteration,
            "previous_evaluations": previous_evaluations,
            "running_processes_count": running_processes_count,
            
            "task": task,
            "initiative": initiative,
            "business": business,
            "llm_requests": iteration.llmrequest_set.order_by("-timestamp"),
            
            "total_price": total_price,
            "total_tokens": total_tokens,
            "running_processes": running_processes
        },
        breadcrumbs=[
            (reverse(view_businesses), Business.get_erie_iron_business().name),
            (reverse(view_business, args=[business.id]), business.name),
            (reverse(view_initiative, args=[initiative.id]), initiative.title),
            (reverse(view_task, args=[task.id]), task.get_name()),
            (reverse(view_self_driver_iteration, args=[iteration.id]), f"Iteration {iteration.version_number}")
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


def action_task_regenerate_test(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    
    # PubSubManager.publish_id(
    #     PubSubMessageType.RESET_TASK_TEST,
    #     task_id
    # )
    on_reset_task_test(task_id)
    
    return redirect(reverse('view_task_tab', args=['testcode', task_id]))


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
    
    try:
        common.delete_dir(task.selfdrivingtask.sandbox_path)
    except:
        ...
    SelfDrivingTaskIteration.objects.filter(self_driving_task__task_id=task_id).delete()
    CodeFile.objects.filter(business_id=task.initiative.business_id).delete()
    
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


def action_business_regenerate_architecture(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    
    # PubSubManager.publish_id(
    #     PubSubMessageType.RESET_TASK_TEST,
    #     business_id
    # )
    eng_lead.write_business_architecture(business)
    
    return redirect(reverse('view_business_tab', args=['architecture', business_id]))


def action_initiative_regenerate_architecture(request, initiative_id):
    initiative = get_object_or_404(Initiative, pk=initiative_id)
    
    # PubSubManager.publish_id(
    #     PubSubMessageType.RESET_TASK_TEST,
    #     initiative_id
    # )
    eng_lead.write_initiative_architecture(initiative)
    
    return redirect(reverse('view_initiative', args=[initiative_id]) + "#testcode")


def action_initiative_regenerate_tasks(request, initiative_id):
    initiative = get_object_or_404(Initiative, pk=initiative_id)
    
    initiative.tasks.all().delete()
    eng_lead.define_tasks_for_initiative(initiative_id)
    
    return redirect(reverse('view_initiative', args=[initiative_id]) + "#testcode")


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


def action_update_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()
    
    try:
        initiative = get_object_or_404(Initiative, pk=initiative_id)
        
        # Get form data
        title = rget(request, 'title', '').strip()
        description = rget(request, 'description', '').strip()
        architecture = rget(request, 'architecture', '').strip()
        requires_unit_tests = request.POST.get('requires_unit_tests') == 'on'
        
        # Prepare update data
        update_data = {
            'title': title,
            'description': description,
            'architecture': architecture or None,
            'requires_unit_tests': requires_unit_tests
        }
        
        # Update the initiative
        Initiative.objects.filter(id=initiative_id).update(**update_data)
        
        messages.success(request, 'Initiative updated successfully!')
        return redirect(reverse('view_initiative', args=[initiative_id]) + '#edit')
    except Initiative.DoesNotExist:
        messages.error(request, 'Initiative not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error updating initiative: {str(e)}')
        return redirect(reverse('view_initiative', args=[initiative_id]))


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
        return redirect(reverse('view_task_tab', args=['guidance', task_id]))
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
                return redirect(reverse('view_task_tab', args=['edit', task_id]))
        else:
            update_data['timeout_seconds'] = None
        
        if max_budget_usd:
            try:
                # noinspection PyTypedDict
                update_data['max_budget_usd'] = float(max_budget_usd)
            except ValueError:
                messages.error(request, 'Invalid budget value.')
                return redirect(reverse('view_task_tab', args=['edit', task_id]))
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
                return redirect(reverse('view_task_tab', args=['edit', task_id]))
        else:
            update_data['execution_start_time'] = None
        
        # Update the task
        Task.objects.filter(id=task_id).update(**update_data)
        
        messages.success(request, 'Task updated successfully!')
        return redirect(reverse('view_task_tab', args=['edit', task_id]))
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
        architecture = rget(request, 'architecture', '').strip()
        allow_autonomous_shutdown = request.POST.get('allow_autonomous_shutdown') == 'on'
        needs_domain = request.POST.get('needs_domain') == 'on'
        
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
            'architecture': architecture or None,
            'allow_autonomous_shutdown': allow_autonomous_shutdown,
            'needs_domain': needs_domain
        }
        
        # Handle optional autonomy_level
        if autonomy_level:
            update_data['autonomy_level'] = autonomy_level
        else:
            update_data['autonomy_level'] = None
        
        # Update the business
        Business.objects.filter(id=business_id).update(**update_data)
        
        messages.success(request, 'Business updated successfully!')
        return redirect(reverse('view_business_tab', args=['edit', business_id]))
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
        return redirect(reverse('view_business_tab', args=['edit', business_id]))
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
            redirect_url = reverse('view_task_tab', args=['processes', task_id])
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
        task = iteration.self_driving_task.task
        task_id = task.id
        
        iteration.delete()
        
        messages.success(request, f'Iteration {iteration_id} deleted successfully!')
        return redirect(reverse('view_task_tab', args=['iterations', task_id]))
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


@json_endpoint
def action_llm_debug_compare(request, llm_request_id):
    llm_model_unparsed = rget(request, "llm_model")
    llm_model, verbosity, reasoning_effort = llm_model_unparsed.split(";")
    
    orig_llm_request = LlmRequest.objects.get(id=llm_request_id)
    
    resp = system_agent_llm_interface.llm_chat(
        description=f"Compare {orig_llm_request.title} response using {llm_model_unparsed}",
        messages=orig_llm_request.input_messages,
        model=LlmModel(llm_model),
        tag_entity=(
                orig_llm_request.task_iteration
                or orig_llm_request.initiative
                or orig_llm_request.business
                or Business.get_erie_iron_business()
        ),
        reasoning_effort=LlmReasoningEffort(reasoning_effort),
        verbosity=LlmVerbosity(verbosity)
    )
    
    diff_lines = difflib.unified_diff(
        orig_llm_request.response.splitlines(),
        resp.text.splitlines(),
        fromfile=f"Model: {orig_llm_request.llm_model}; Reasoning: {orig_llm_request.reasoning_effort}; Verbosity: {orig_llm_request.verbosity}",
        tofile=f"Model: {resp.model}; Reasoning: {reasoning_effort}; Verbosity: {verbosity}",
        lineterm=""
    )
    diff = "\n".join(diff_lines)
    
    return {
        "url": reverse(view_llm_request, args=[resp.llm_request_id]),
        "diff": diff,
        "chat_millis": f"{int(resp.chat_millis):,}ms",
        "price": f"${resp.price_total:.3f}",
        "llm_response_text": escape(resp.text)
    }


def action_llm_debug_ask(request, llm_request_id):
    optimize = rget_bool(request, "optimize")
    prompt = rget(request, "prompt")
    llm_model, verbosity, reasoning_effort = rget(request, "llm_model").split(";")
    
    orig_llm_request = LlmRequest.objects.get(id=llm_request_id)
    
    resp = system_agent_llm_interface.llm_chat(
        description=f"{'Optimize' if optimize else 'Debug'} {orig_llm_request.title}",
        messages=[
            get_sys_prompt("chat_evaluator.md" if optimize else "chat_response_interpreter.md"),
            LlmMessage.user_from_data(
                "Chat Interatction",
                orig_llm_request.get_llm_data()
            ),
            prompt
        ],
        model=LlmModel(llm_model),
        tag_entity=(
                orig_llm_request.task_iteration
                or orig_llm_request.initiative
                or orig_llm_request.business
                or Business.get_erie_iron_business()
        ),
        reasoning_effort=LlmReasoningEffort(reasoning_effort),
        verbosity=LlmVerbosity(verbosity)
    )
    
    return send_response(
        request,
        "_llm_request_response.html",
        {
            "llm_response": resp
        }
    )


def view_llm_request(request, llm_request_id):
    llm_request = LlmRequest.objects.get(id=llm_request_id)
    
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name)
    ]
    if llm_request.business_id:
        breadcrumbs.append(
            (reverse('view_business_tab', args=['product-initiatives', llm_request.business_id]), llm_request.business.name),
        )
        if llm_request.initiative:
            breadcrumbs.append(
                (f"{reverse(view_initiative, args=[llm_request.initiative_id])}#tasks", llm_request.initiative.title),
            )
            if llm_request.task_iteration:
                task = llm_request.task_iteration.self_driving_task.task
                breadcrumbs.append(
                    (f"{reverse(view_task, args=[task.id])}#iterations", task.get_name())
                )
                breadcrumbs.append(
                    (f"{reverse(view_self_driver_iteration, args=[llm_request.task_iteration_id])}#", f"Iteration {llm_request.task_iteration.version_number}")
                )
    
    model_choices = []
    for m in LlmModel:
        if m == LlmModel.OPENAI_GPT_5:
            for verbosity in LlmVerbosity:
                for reasoning_effort in LlmReasoningEffort:
                    model_choices.append({
                        "label": f"{m.label()} - {verbosity.label()} Verbosity, {reasoning_effort.label()} Reasoning",
                        "value": f"{m.value};{verbosity.value};{reasoning_effort.value}"
                    })
        else:
            model_choices.append({
                "label": m.label(),
                "value": f"{m.value};{LlmVerbosity.MEDIUM};{LlmReasoningEffort.MEDIUM}"
            })
    
    return send_response(
        request,
        "llm_request.html",
        {
            "llm_request": llm_request,
            "model_choices": model_choices,
            "model_choice_value": f"{LlmModel.OPENAI_GPT_5.value};{LlmVerbosity.MEDIUM};{LlmReasoningEffort.MEDIUM}"
        },
        breadcrumbs=breadcrumbs
    )


TAB_DIVIDER = {
    "slug": "divider",
    "is_divider": True
}

TASK_TAB_DEFINITIONS = [
    {
        "slug": "overview",
        "label": "Overview",
        "template": "task/tabs/overview.html",
        "availability_fn": _task_tab_available_overview,
        "context_fn": _task_tab_context_overview,
    },
    {
        "slug": "blocked-by",
        "label": "Blocked By",
        "template": "task/tabs/blocked_by.html",
        "availability_fn": _task_tab_available_blocked_by,
        "context_fn": _task_tab_context_blocked_by,
    },
    {
        "slug": "blocks",
        "label": "Blocks",
        "template": "task/tabs/blocks.html",
        "availability_fn": _task_tab_available_blocks,
        "context_fn": _task_tab_context_blocks,
    },
    TAB_DIVIDER,
    {
        "slug": "iterations",
        "label": "Code Iterations",
        "template": "task/tabs/iterations.html",
        "availability_fn": _task_tab_available_iterations,
        "context_fn": _task_tab_context_iterations,
    },
    {
        "slug": "latest_iteration",
        "label": "Latest Iteration",
        "template": "task/tabs/iterations.html",
        "availability_fn": _task_tab_available_iterations,
        "context_fn": _task_tab_context_latest_iteration,
    },
    TAB_DIVIDER,
    {
        "slug": "guidance",
        "label": "Guidance",
        "template": "task/tabs/guidance.html",
        "availability_fn": _task_tab_available_guidance,
        "context_fn": _task_tab_context_guidance,
    },
    {
        "slug": "testcode",
        "label": "Test Code",
        "template": "task/tabs/testcode.html",
        "availability_fn": _task_tab_available_testcode,
        "context_fn": _task_tab_context_testcode,
    },
    TAB_DIVIDER,
    {
        "slug": "resolve",
        "label": "Resolve",
        "template": "task/tabs/resolve.html",
        "availability_fn": _task_tab_available_resolve,
        "context_fn": _task_tab_context_resolve,
    },
    {
        "slug": "executions",
        "label": "Executions",
        "template": "task/tabs/executions.html",
        "availability_fn": _task_tab_available_executions,
        "context_fn": _task_tab_context_executions,
    },
    # {
    #     "slug": "processes",
    #     "label": "Processes",
    #     "template": "task/tabs/processes.html",
    #     "availability_fn": _task_tab_available_processes,
    #     "context_fn": _task_tab_context_processes,
    # },
    {
        "slug": "llmrequests",
        "label": "LLM Requests",
        "template": "task/tabs/llmrequests.html",
        "availability_fn": _task_tab_available_llmrequests,
        "context_fn": _task_tab_context_llmrequests,
    },
    TAB_DIVIDER,
    {
        "slug": "edit",
        "label": "Edit",
        "template": "task/tabs/edit.html",
        "availability_fn": _task_tab_available_edit,
        "context_fn": _task_tab_context_edit,
    },
]

BUSINESS_TAB_DEFINITIONS = [
    {
        "slug": "overview",
        "label": "Overview",
        "template": "business/tabs/overview.html",
        "availability_fn": _tab_available_overview,
        "context_fn": _tab_context_overview,
    },
    TAB_DIVIDER,
    {
        "slug": "business-plan",
        "label": "Business Plan",
        "template": "business/tabs/business_plan.html",
        "availability_fn": _tab_available_business_plan,
        "context_fn": _tab_context_business_plan,
    },
    {
        "slug": "business-analysis",
        "label": "Business Analysis",
        "template": "business/tabs/business_analysis.html",
        "availability_fn": _tab_available_business_analysis,
        "context_fn": _tab_context_business_analysis,
    },
    {
        "slug": "legal-analysis",
        "label": "Legal Analysis",
        "template": "business/tabs/legal_analysis.html",
        "availability_fn": _tab_available_legal_analysis,
        "context_fn": _tab_context_legal_analysis,
    },
    {
        "slug": "capacity-analysis",
        "label": "Capacity Analysis",
        "template": "business/tabs/capacity_analysis.html",
        "availability_fn": _tab_available_capacity_analysis,
        "context_fn": _tab_context_capacity_analysis,
    },
    TAB_DIVIDER,
    {
        "slug": "architecture",
        "label": "Architecture",
        "template": "business/tabs/architecture.html",
        "availability_fn": _tab_available_architecture,
        "context_fn": _tab_context_architecture,
    },
    {
        "slug": "product-initiatives",
        "label": "Product Initiatives",
        "template": "business/tabs/product_initiatives.html",
        "availability_fn": _tab_available_product_initiatives,
        "context_fn": _tab_context_product_initiatives,
    },
    {
        "slug": "board-guidance",
        "label": "Board Guidance",
        "template": "business/tabs/board_guidance.html",
        "availability_fn": _tab_available_board_guidance,
        "context_fn": _tab_context_board_guidance,
    },
    TAB_DIVIDER,
    {
        "slug": "ceo-guidance",
        "label": "CEO Guidance",
        "template": "business/tabs/ceo_guidance.html",
        "availability_fn": _tab_available_ceo_guidance,
        "context_fn": _tab_context_ceo_guidance,
    },
    {
        "slug": "llmrequests",
        "label": "LLM Requests",
        "template": "business/tabs/llmrequests.html",
        "availability_fn": _tab_available_llmrequests,
        "context_fn": _tab_context_llmrequests,
    },
    {
        "slug": "tasks",
        "label": "Tasks",
        "template": "business/tabs/tasks.html",
        "availability_fn": _tab_available_tasks,
        "context_fn": _tab_context_tasks,
    },
    TAB_DIVIDER,
    {
        "slug": "edit",
        "label": "Edit",
        "template": "business/tabs/edit.html",
        "availability_fn": _tab_available_edit,
        "context_fn": _tab_context_edit,
    },
]

BUSINESS_TAB_MAP = {definition["slug"]: definition for definition in BUSINESS_TAB_DEFINITIONS if "slug" in definition}
TASK_TAB_MAP = {definition["slug"]: definition for definition in TASK_TAB_DEFINITIONS if "slug" in definition}
