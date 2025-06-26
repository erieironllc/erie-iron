from collections import defaultdict

from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from erieiron_common import common
from erieiron_common.models import Task, ProductInitiative, Business, SelfDrivingTask
from erieiron_ui.view_utils import send_response


def hello(request):
    return HttpResponse("hello world")


def view_tasks(request):
    task_map = {t.id: t for t in Task.objects.all()}
    initiativeid_to_tasks = defaultdict(list)
    for t in Task.objects.order_by("created_timestamp"):
        initiativeid_to_tasks[t.product_initiative_id].append(t)

    businessid_to_initiatives = defaultdict(list)
    for initiative in ProductInitiative.objects.filter(id__in=initiativeid_to_tasks).order_by("created_timestamp"):
        businessid_to_initiatives[initiative.business_id].append(initiative)

    businesses = []
    for b in Business.objects.all().order_by("created_at"):
        business_data = common.get_dict(b)
        has_tasks = False

        init_datas = business_data["initiatives"] = []
        for initiative in businessid_to_initiatives[b.id]:
            init_data = common.get_dict(initiative)
            init_datas.append(init_data)

            task_datas = init_data["tasks"] = []
            for task in initiativeid_to_tasks[initiative.id]:
                has_tasks = True
                task_data = common.get_dict(task)

                task_datas.append(task)

        if has_tasks:
            businesses.append(business_data)

    return send_response(request, "tasks.html", {
        "businesses": businesses
    })


def view_task(request, task_id):
    task = get_object_or_404(Task, pk=task_id)

    return send_response(request, "task.html", {
        "selfdrivingtask": SelfDrivingTask.objects.filter(related_task_id=task_id).first(),
        "task": task
    })
