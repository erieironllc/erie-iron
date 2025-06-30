import json
from collections import defaultdict

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from erieiron_common import common
from erieiron_common.enums import TaskStatus, PubSubMessageType, BusinessIdeaSource, Constants
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import Task, Initiative, Business, SelfDrivingTask, SelfDrivingTaskIteration
from erieiron_ui.view_utils import send_response, redirect, rget


def hello(request):
    return HttpResponse("hello world")


def view_businesses(request):
    erieiron_business = Business.get_erie_iron_business()

    return send_response(
        request, "businesses.html", {
            "erieiron_business": erieiron_business,
            "businesses": Business.objects.exclude(id=erieiron_business.id).order_by("created_at")
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

    task_type_tasks = defaultdict(list)
    for task in initiative.tasks.all():
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

    self_driving_task = SelfDrivingTask.objects.filter(related_task_id=task_id).first()
    if self_driving_task:
        iterations = self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp")
    else:
        iterations = []

    return send_response(
        request, "task.html",
        {
            "task_executions": list(task.taskexecution_set.order_by("-executed_time")),
            "iterations": iterations,
            "self_driving_task": self_driving_task,
            "task": task
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

    return send_response(
        request, "iteration.html",
        {
            "task": task,
            "initiative": initiative,
            "business": business,

            "total_price": total_price,
            "total_tokens": total_tokens,
            "iteration": iteration
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
