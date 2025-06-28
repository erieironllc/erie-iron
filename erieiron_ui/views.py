import json
from collections import defaultdict

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from erieiron_common.enums import TaskStatus, PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import Task, ProductInitiative, Business, SelfDrivingTask, SelfDrivingTaskIteration
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
    tasks = Task.objects.filter(product_initiative__business=business)

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
    initiative = get_object_or_404(ProductInitiative, pk=initiative_id)
    business = initiative.business

    task_type_tasks = defaultdict(list)
    for task in initiative.engineering_tasks.all():
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
    initiative = task.product_initiative
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
    initiative = task.product_initiative
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
