import difflib
import json
import logging
import pprint
import uuid
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone, formats
from django.utils.html import escape
from django.views.decorators.http import require_POST

from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.coding_agents.self_driving_coder_agent import on_reset_task_test
from erieiron_autonomous_agent.enums import TaskStatus, BusinessStatus, BusinessOperationType
from erieiron_autonomous_agent.models import (
    Business,
    LlmRequest,
    AgentLesson,
    CodeFile,
    CodeVersion,
    InfrastructureStack,
)
from erieiron_autonomous_agent.models import Task, Initiative, SelfDrivingTask, SelfDrivingTaskIteration, TaskExecution, RunningProcess
from erieiron_autonomous_agent.system_agent_llm_interface import get_sys_prompt
from erieiron_common import common, domain_manager
from erieiron_common.enums import PubSubMessageType, BusinessIdeaSource, Constants, TaskExecutionSchedule, TaskType, Level, LlmModel, LlmVerbosity, LlmReasoningEffort, Role, InfrastructureStackType, AwsEnv
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.view_utils import send_response, redirect, rget, rget_bool, rget_int, json_endpoint, rget_list, rget_json

LLM_SPEND_RANGE_DEFAULT = "15d"
LLM_SPEND_RANGE_OPTIONS = [
    {"slug": "24h", "label": "Last 24 Hours"},
    {"slug": "15d", "label": "Last 15 Days"},
    {"slug": "30d", "label": "Last 30 Days"},
    {"slug": "all", "label": "All Time"},
]


def hello(request):
    return HttpResponse("hello world")


def _businesses_tab_available_portfolio(_: Business) -> bool:
    return True


def _businesses_tab_context_portfolio(erieiron_business: Business) -> dict:
    return {
        "businesses": Business.objects.exclude(id=erieiron_business.id).order_by("created_at"),
    }


def _businesses_tab_available_capacity(erieiron_business: Business) -> bool:
    return erieiron_business.businesscapacityanalysis_set.exists()


def _businesses_tab_context_capacity(erieiron_business: Business) -> dict:
    return {
        "business_capacity_analysis_list": erieiron_business.businesscapacityanalysis_set.all().order_by("-created_timestamp"),
    }


def _businesses_tab_available_initiatives(erieiron_business: Business) -> bool:
    return erieiron_business.initiative_set.exists()


def _businesses_tab_context_initiatives(erieiron_business: Business) -> dict:
    return {
        "initiatives": erieiron_business.initiative_set.all().order_by("created_timestamp"),
    }


def _businesses_tab_available_lessons(_: Business) -> bool:
    return AgentLesson.objects.exists()


def _businesses_tab_context_lessons(_: Business) -> dict:
    return {
        "agent_lessons": AgentLesson.objects.all().order_by("-timestamp"),
    }


def _businesses_tab_available_tools(_: Business) -> bool:
    return True


def _businesses_tab_context_tools(_: Business) -> dict:
    return {
        "all_running_processes": RunningProcess.objects.filter(is_running=True).order_by('-started_at'),
        "operation_type_choices": BusinessOperationType.choices(),
        "operation_type_default": BusinessOperationType.ERIE_IRON_AUTONOMOUS.value,
    }


def _resolve_llm_vendor(llm_model: str | None) -> str:
    if not llm_model:
        return "unknown"
    
    normalized = (llm_model or "").strip().lower()
    enum_name = ""
    try:
        enum_name = LlmModel(llm_model).name.lower()
    except (ValueError, TypeError):
        enum_name = ""
    
    candidates = [enum_name, normalized]
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.startswith(("openai", "gpt", "o1", "o3", "o4")):
            return "openai"
        if candidate.startswith("gemini"):
            return "gemini"
        if candidate.startswith(("claude", "anthropic")):
            return "anthropic"
        if candidate.startswith("deepseek"):
            return "deepseek"
    
    if "gemini" in normalized:
        return "gemini"
    if "claude" in normalized or "anthropic" in normalized:
        return "anthropic"
    if "deepseek" in normalized:
        return "deepseek"
    if normalized.startswith("o"):
        return "openai"
    return "other"


def _resolve_llm_title_group(title: str | None) -> str:
    val = (title or "Untitled").strip()
    lower_val = val.lower()
    
    prefix_map = {
        "compare write initial test": "Compare Write initial test",
        "debug": "Debug",
        "write code": "Write code",
    }
    
    plan_exact_set = {
        "plan aws provisioning code changes",
        "plan quick fix code changes",
        "plan code changes",
    }
    
    if lower_val in plan_exact_set:
        return "Plan Changes"
    
    for prefix, label in prefix_map.items():
        if lower_val.startswith(prefix):
            return label
    
    return val or "Untitled"


def _start_of_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _businesses_tab_available_llm_spend(_: Business) -> bool:
    return True


def _build_llm_spend_context(
        business: Business | None = None,
        request=None,
        initiative: Initiative | None = None,
        task: Task | None = None,
        enable_range_selection: bool = True,
) -> dict:
    selected_range = LLM_SPEND_RANGE_DEFAULT
    if enable_range_selection and request:
        selected_candidate = (request.GET.get("range") or "").lower()
        if any(option["slug"] == selected_candidate for option in LLM_SPEND_RANGE_OPTIONS):
            selected_range = selected_candidate
    
    if not enable_range_selection:
        selected_range = "custom"
    
    bucket_type = "day"
    now_dt = timezone.now()
    today = timezone.localdate()
    current_tz = timezone.get_current_timezone()
    
    requests_qs = LlmRequest.objects.exclude(price__isnull=True)
    if business:
        requests_qs = requests_qs.filter(business=business)
    if initiative:
        requests_qs = requests_qs.filter(initiative=initiative)
    if task:
        requests_qs = requests_qs.filter(task_iteration__self_driving_task__task=task)
    
    bucket_keys: list = []
    
    if not enable_range_selection:
        bucket_type = "hour"
        first_ts = requests_qs.order_by("timestamp").values_list("timestamp", flat=True).first()
        last_ts = requests_qs.order_by("-timestamp").values_list("timestamp", flat=True).first()
        
        if first_ts and last_ts:
            start_bucket = timezone.localtime(first_ts, current_tz).replace(minute=0, second=0, microsecond=0)
            end_bucket = timezone.localtime(last_ts, current_tz).replace(minute=0, second=0, microsecond=0)
        else:
            start_bucket = timezone.localtime(now_dt, current_tz).replace(minute=0, second=0, microsecond=0)
            end_bucket = start_bucket
        
        if start_bucket > end_bucket:
            start_bucket, end_bucket = end_bucket, start_bucket
        
        requests_qs = requests_qs.filter(
            timestamp__gte=start_bucket,
            timestamp__lt=end_bucket + timedelta(hours=1)
        )
        
        current = start_bucket
        while current <= end_bucket:
            bucket_keys.append(current)
            current += timedelta(hours=1)
    
    elif selected_range == "24h":
        bucket_type = "hour"
        end_bucket = timezone.localtime(now_dt, current_tz).replace(minute=0, second=0, microsecond=0)
        start_bucket = end_bucket - timedelta(hours=23)
        requests_qs = requests_qs.filter(timestamp__gte=start_bucket)
        
        current = start_bucket
        while current <= end_bucket:
            bucket_keys.append(current)
            current += timedelta(hours=1)
    elif selected_range == "30d":
        bucket_type = "day"
        window_end_date = today
        window_start_date = window_end_date - timedelta(days=29)
        requests_qs = requests_qs.filter(timestamp__date__gte=window_start_date)
        
        current = window_start_date
        while current <= window_end_date:
            bucket_keys.append(current)
            current += timedelta(days=1)
    elif selected_range == "all":
        bucket_type = "week"
        first_timestamp = requests_qs.order_by("timestamp").values_list("timestamp", flat=True).first()
        if first_timestamp:
            first_date = timezone.localtime(first_timestamp, current_tz).date()
        else:
            first_date = today
        
        start_week = _start_of_week(first_date)
        end_week = _start_of_week(today)
        
        current = start_week
        while current <= end_week:
            bucket_keys.append(current)
            current += timedelta(weeks=1)
    else:  # default last 15 days
        bucket_type = "day"
        window_end_date = today
        window_start_date = window_end_date - timedelta(days=14)
        requests_qs = requests_qs.filter(timestamp__date__gte=window_start_date)
        
        current = window_start_date
        while current <= window_end_date:
            bucket_keys.append(current)
            current += timedelta(days=1)
    
    if not bucket_keys:
        if bucket_type == "hour":
            bucket_keys = [timezone.localtime(now_dt, current_tz).replace(minute=0, second=0, microsecond=0)]
        elif bucket_type == "week":
            bucket_keys = [_start_of_week(today)]
        else:
            bucket_keys = [today]
    
    totals_by_bucket: dict = defaultdict(float)
    vendor_daily: dict = defaultdict(lambda: defaultdict(float))
    business_daily: dict = defaultdict(lambda: defaultdict(float))
    title_daily: dict = defaultdict(lambda: defaultdict(float))
    initiative_daily: dict = defaultdict(lambda: defaultdict(float))
    task_daily: dict = defaultdict(lambda: defaultdict(float))
    iteration_daily: dict = defaultdict(lambda: defaultdict(float))
    
    request_fields = [
        "timestamp",
        "price",
        "llm_model",
        "business__name",
        "business__id",
        "initiative__id",
        "initiative__title",
        "task_iteration__self_driving_task__task__id",
        "task_iteration__self_driving_task__task__description",
        "task_iteration__id",
        "task_iteration__version_number",
        "title",
    ]
    
    for record in requests_qs.values(*request_fields):
        timestamp = record.get("timestamp")
        if not timestamp:
            continue
        
        local_ts = timezone.localtime(timestamp, current_tz)
        if bucket_type == "hour":
            bucket = local_ts.replace(minute=0, second=0, microsecond=0)
        elif bucket_type == "week":
            bucket = _start_of_week(local_ts.date())
        else:
            bucket = local_ts.date()
        
        price = float(record.get("price") or 0.0)
        
        totals_by_bucket[bucket] += price
        
        vendor = _resolve_llm_vendor(record.get("llm_model"))
        vendor_daily[vendor][bucket] += price
        
        business_name = record.get("business__name") or "Unassigned"
        business_id = record.get("business__id")
        business_key = (business_id, business_name)
        business_daily[business_key][bucket] += price
        
        initiative_id = record.get("initiative__id")
        initiative_title = record.get("initiative__title") or "Unassigned"
        initiative_key = (initiative_id, initiative_title)
        initiative_daily[initiative_key][bucket] += price
        
        task_id = record.get("task_iteration__self_driving_task__task__id")
        task_label = record.get("task_iteration__self_driving_task__task__id") or "Task"
        
        if "task_" in task_label:
            task_label = task_label[len("task_"):]
        
        task_label = task_label.split("--")[-1]
        
        task_label = (task_label
                      .replace("_", " ")
                      .replace("-", " ")
                      .capitalize())
        
        if task_id:
            task_key = (task_id, task_label)
            task_daily[task_key][bucket] += price
        
        iteration_id = record.get("task_iteration__id")
        iteration_version = record.get("task_iteration__version_number")
        if iteration_id:
            iteration_label = f"Iteration {iteration_version}" if iteration_version is not None else f"Iteration {iteration_id}"
            iteration_key = (iteration_id, iteration_label)
            iteration_daily[iteration_key][bucket] += price
        
        title_value = _resolve_llm_title_group(record.get("title"))
        title_daily[title_value][bucket] += price
    
    def _bucket_iso(bucket_value):
        if isinstance(bucket_value, datetime):
            return bucket_value.isoformat()
        return bucket_value.isoformat()
    
    total_series = [
        {
            "date": _bucket_iso(bucket),
            "total": round(totals_by_bucket.get(bucket, 0.0), 4),
        }
        for bucket in bucket_keys
    ]
    
    vendor_series: list[dict] = []
    for vendor, bucket_map in sorted(vendor_daily.items()):
        vendor_total = 0.0
        points = []
        for bucket in bucket_keys:
            amount = round(bucket_map.get(bucket, 0.0), 4)
            vendor_total += amount
            points.append({
                "date": _bucket_iso(bucket),
                "total": amount,
            })
        
        if vendor_total > 0:
            vendor_series.append({
                "vendor": vendor,
                "points": points,
                "total": round(vendor_total, 4),
            })
    
    business_series: list[dict] = []
    for business_key, bucket_map in sorted(business_daily.items(), key=lambda item: (item[0][1] or "").lower()):
        business_id, business_name = business_key
        business_total = 0.0
        points = []
        for bucket in bucket_keys:
            amount = round(bucket_map.get(bucket, 0.0), 4)
            business_total += amount
            points.append({
                "date": _bucket_iso(bucket),
                "total": amount,
            })
        
        if business_total > 0:
            business_series.append({
                "business": business_name,
                "business_id": business_id,
                "points": points,
                "total": round(business_total, 4),
            })
    
    title_series: list[dict] = []
    for title_name, bucket_map in title_daily.items():
        title_total = 0.0
        points = []
        for bucket in bucket_keys:
            amount = round(bucket_map.get(bucket, 0.0), 4)
            title_total += amount
            points.append({
                "date": _bucket_iso(bucket),
                "total": amount,
            })
        
        if title_total > 0:
            title_series.append({
                "title": title_name,
                "points": points,
                "total": round(title_total, 4),
            })
    
    title_series.sort(key=lambda entry: entry["total"], reverse=True)
    
    initiative_series: list[dict] = []
    for initiative_key, bucket_map in sorted(initiative_daily.items(), key=lambda item: (item[0][1] or "").lower()):
        initiative_id, initiative_title = initiative_key
        initiative_total = 0.0
        points = []
        for bucket in bucket_keys:
            amount = round(bucket_map.get(bucket, 0.0), 4)
            initiative_total += amount
            points.append({
                "date": _bucket_iso(bucket),
                "total": amount,
            })
        
        if initiative_total > 0:
            initiative_series.append({
                "initiative": initiative_title,
                "initiative_id": initiative_id,
                "points": points,
                "total": round(initiative_total, 4),
            })
    
    initiative_series.sort(key=lambda entry: entry["total"], reverse=True)
    
    task_series: list[dict] = []
    for task_key, bucket_map in sorted(task_daily.items(), key=lambda item: (item[0][1] or "").lower()):
        task_id, task_label = task_key
        task_total = 0.0
        points = []
        for bucket in bucket_keys:
            amount = round(bucket_map.get(bucket, 0.0), 4)
            task_total += amount
            points.append({
                "date": _bucket_iso(bucket),
                "total": amount,
            })
        
        if task_total > 0:
            task_series.append({
                "task": task_label,
                "task_id": task_id,
                "points": points,
                "total": round(task_total, 4),
            })
    
    task_series.sort(key=lambda entry: entry["total"], reverse=True)
    
    iteration_series: list[dict] = []
    for iteration_key, bucket_map in iteration_daily.items():
        iteration_id, iteration_label = iteration_key
        iteration_total = 0.0
        points = []
        for bucket in bucket_keys:
            amount = round(bucket_map.get(bucket, 0.0), 4)
            iteration_total += amount
            points.append({
                "date": _bucket_iso(bucket),
                "total": amount,
            })
        
        if iteration_total > 0:
            iteration_series.append({
                "iteration": iteration_label,
                "iteration_id": iteration_id,
                "points": points,
                "total": round(iteration_total, 4),
            })
    
    iteration_series.sort(key=lambda entry: entry["total"], reverse=True)
    
    overall_total = round(sum(entry["total"] for entry in total_series), 4)
    has_data = any(series_point["total"] > 0 for series_point in total_series)
    
    show_initiative_series = (initiative is None and task is None)
    show_task_series = (initiative is not None and task is None)
    show_iteration_series = task is not None
    
    return {
        "llm_spend_total_series": total_series,
        "llm_spend_vendor_series": vendor_series,
        "llm_spend_business_series": business_series if (business is None and initiative is None and task is None) else [],
        "llm_spend_initiative_series": initiative_series if show_initiative_series else [],
        "llm_spend_task_series": task_series if show_task_series else [],
        "llm_spend_iteration_series": iteration_series if show_iteration_series else [],
        "llm_spend_title_series": title_series,
        "llm_spend_has_data": has_data,
        "llm_spend_total_amount": overall_total,
        "llm_spend_range_options": LLM_SPEND_RANGE_OPTIONS if enable_range_selection else [],
        "llm_spend_selected_range": selected_range,
        "llm_spend_bucket_type": bucket_type,
        "llm_spend_show_range_selector": enable_range_selection,
    }


def _businesses_tab_context_llm_spend(_: Business, request=None) -> dict:
    return _build_llm_spend_context(request=request)


def _tab_available_llm_spend(business: Business) -> bool:
    return business.llmrequest_set.exists()


def _tab_context_llm_spend(business: Business, request=None) -> dict:
    return _build_llm_spend_context(business=business, request=request)


def _build_businesses_tabs(erieiron_business: Business) -> list[dict]:
    from erieiron_ui import tab_defitions
    tabs: list[dict] = []
    
    for definition in tab_defitions.BUSINESSES_TAB_DEFINITIONS:
        if definition.get("is_divider"):
            tabs.append(definition)
            continue
        
        slug = definition["slug"]
        available = definition["availability_fn"](erieiron_business)
        if slug == "portfolio":
            url = reverse('view_businesses')
        else:
            url = reverse('view_businesses_tab', args=[slug])
        
        tab_entry = {
            **definition,
            "url": url,
            "available": available,
        }
        
        tabs.append(tab_entry)
    
    return tabs


def view_businesses(request, tab: str = 'portfolio'):
    from erieiron_ui import tab_defitions
    erieiron_business = Business.get_erie_iron_business()
    tab_slug = (tab or 'portfolio').lower()
    
    if tab_slug not in tab_defitions.BUSINESSES_TAB_MAP:
        raise Http404
    
    tabs = _build_businesses_tabs(erieiron_business)
    tab_definition = tab_defitions.BUSINESSES_TAB_MAP[tab_slug]
    
    active_tab_entry = next((t for t in tabs if t.get('slug') == tab_slug), None)
    if not active_tab_entry or not active_tab_entry.get('available'):
        raise Http404
    
    context = {
        "erieiron_business": erieiron_business,
        "tabs": tabs,
        "active_tab": tab_slug,
        "tab_template": tab_definition["template"],
        "sidebar_title": "Erie Iron",
    }
    if tab_slug == 'llm-spend':
        context.update(_businesses_tab_context_llm_spend(erieiron_business, request=request))
    else:
        context.update(tab_definition["context_fn"](erieiron_business))
    
    breadcrumbs = [
        (reverse(view_businesses), erieiron_business.name)
    ]
    if tab_slug != 'portfolio':
        breadcrumbs.append((reverse('view_businesses_tab', args=[tab_slug]), tab_definition["label"]))
    
    return send_response(
        request,
        "businesses/businesses_base.html",
        context,
        breadcrumbs=breadcrumbs
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
    from erieiron_autonomous_agent.business_level_agents.eng_lead import INITIATIVE_TITLE_BOOTSTRAP_ENVS
    return business.initiative_set.exclude(title="BOOTSTRAP_ENVS").exclude(title=INITIATIVE_TITLE_BOOTSTRAP_ENVS).exists()


def _tab_context_product_initiatives(business: Business) -> dict:
    from erieiron_autonomous_agent.business_level_agents.eng_lead import INITIATIVE_TITLE_BOOTSTRAP_ENVS
    return {
        "initiatives": business.initiative_set.exclude(title="BOOTSTRAP_ENVS").exclude(title=INITIATIVE_TITLE_BOOTSTRAP_ENVS).order_by("created_timestamp")
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


def _tab_available_codefiles(business: Business) -> bool:
    return CodeVersion.objects.filter(
        code_file__business=business
    ).exists()


def _tab_context_codefiles(business: Business) -> dict:
    code_versions = list(
        CodeVersion.objects
        .filter(code_file__business=business)
        .select_related("code_file", "task_iteration")
        .order_by("code_file__file_path", "task_iteration__timestamp", "id")
    )
    
    if not code_versions:
        return {"code_file_tree": []}
    
    file_entries: dict[str, dict] = {}
    for code_version in code_versions:
        code_file = code_version.code_file
        iteration = code_version.task_iteration
        if not code_file or not iteration:
            continue
        
        file_entry = file_entries.setdefault(
            code_file.file_path,
            {
                "path": code_file.file_path,
                "codefile_id": code_file.id,
                "iterations": OrderedDict(),
            }
        )
        
        file_entry["iterations"][iteration.id] = {
            "id": iteration.id,
            "version_number": iteration.version_number,
            "timestamp": iteration.timestamp,
        }
    
    if not file_entries:
        return {"code_file_tree": []}
    
    tree_root = {
        "name": "",
        "path": "",
        "type": "dir",
        "children": OrderedDict(),
    }
    
    for file_path, entry in sorted(file_entries.items()):
        parts = [part for part in Path(file_path).parts if part]
        if not parts:
            continue
        
        node = tree_root
        ancestry: list[str] = []
        for directory in parts[:-1]:
            ancestry.append(directory)
            children = node.setdefault("children", OrderedDict())
            if directory not in children:
                children[directory] = {
                    "name": directory,
                    "path": "/".join(ancestry),
                    "type": "dir",
                    "children": OrderedDict(),
                }
            node = children[directory]
        
        iterations = list(entry["iterations"].values())
        iterations.sort(key=lambda item: item["version_number"])
        
        node.setdefault("children", OrderedDict())[parts[-1]] = {
            "name": parts[-1],
            "path": file_path,
            "type": "file",
            "codefile_id": entry["codefile_id"],
            "iterations": iterations,
        }
    
    def _ordered_children(node: dict) -> dict:
        children = node.get("children", OrderedDict())
        ordered_children = []
        for child_name, child_node in sorted(
                children.items(),
                key=lambda item: (0 if item[1]["type"] == "dir" else 1, item[0])
        ):
            ordered_children.append(_ordered_children(child_node))
        node["children"] = ordered_children
        return node
    
    ordered_tree = _ordered_children(tree_root)
    
    return {"code_file_tree": ordered_tree["children"]}


def _build_business_tabs(business: Business) -> list[dict]:
    from erieiron_ui import tab_defitions
    
    tabs = []
    for definition in tab_defitions.BUSINESS_TAB_DEFINITIONS:
        if definition.get("is_divider"):
            tabs.append(definition)
        else:
            slug = definition["slug"]
            available = definition["availability_fn"](business)
            if slug == "overview":
                url = reverse('view_business', args=[business.id])
            else:
                url = reverse('view_business_tab', args=[slug, business.id])
            tab_data = {
                **definition,
                "url": url,
                "available": available,
            }
            
            if slug == "overview":
                tab_data['label'] = business.name.title()
            
            tabs.append(tab_data)
    
    return tabs


def view_business(request, business_id, tab='overview'):
    from erieiron_ui import tab_defitions
    
    business = get_object_or_404(Business, pk=business_id)
    tab = (tab or 'overview').lower()
    
    if tab not in tab_defitions.BUSINESS_TAB_MAP:
        raise Http404
    
    tabs = _build_business_tabs(business)
    tab_definition = tab_defitions.BUSINESS_TAB_MAP[tab]
    
    is_available = next((t for t in tabs if t['slug'] == tab), None)
    if not is_available or not is_available['available']:
        raise Http404
    
    context = {
        "business": business,
        "tabs": tabs,
        "active_tab": tab,
        "tab_template": tab_definition["template"],
    }
    if tab == 'llm-spend':
        context.update(_tab_context_llm_spend(business, request=request))
    else:
        context.update(tab_definition["context_fn"](business))
    
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name),
        (reverse('view_business', args=[business.id]), business.name)
    ]
    if tab != 'overview':
        breadcrumbs.append((reverse('view_business_tab', args=[tab, business.id]), tab_definition["label"]))
    
    return send_response(
        request,
        "business/business_base.html",
        context,
        breadcrumbs=breadcrumbs
    )


def _initiative_tab_available_overview(initiative: Initiative) -> bool:
    return True


def _initiative_tab_context_overview(initiative: Initiative) -> dict:
    return {}


def _initiative_tab_available_requirements(initiative: Initiative) -> bool:
    return initiative.requirements.exists()


def _initiative_tab_context_requirements(initiative: Initiative) -> dict:
    return {
        "requirements": initiative.requirements.all()
    }


def _initiative_tab_available_architecture(initiative: Initiative) -> bool:
    return bool(initiative.architecture)


def _initiative_tab_context_architecture(initiative: Initiative) -> dict:
    return {}


def _initiative_tab_available_tasks(initiative: Initiative) -> bool:
    return initiative.tasks.exists()


def _initiative_tab_context_tasks(initiative: Initiative) -> dict:
    tasks = list(initiative.tasks.order_by("created_timestamp"))
    if not tasks:
        return {"tasks": tasks}
    
    task_ids = [task.id for task in tasks]
    self_driving_tasks = list(
        SelfDrivingTask.objects.filter(task_id__in=task_ids).order_by("created_at")
    )
    sdt_id_to_task_id = {
        sdt.id: sdt.task_id
        for sdt in self_driving_tasks
        if sdt.task_id
    }
    
    task_iterations = defaultdict(list)
    for iteration in SelfDrivingTaskIteration.objects.filter(
            self_driving_task__task_id__in=task_ids
    ).order_by("timestamp"):
        task_id = sdt_id_to_task_id.get(iteration.self_driving_task_id)
        if task_id:
            task_iterations[task_id].append(iteration)
    
    task_executions = defaultdict(list)
    for execution in TaskExecution.objects.filter(task_id__in=task_ids).order_by("executed_time"):
        task_executions[execution.task_id].append(execution)
    
    for task in tasks:
        iterations = task_iterations.get(task.id, [])
        executions = task_executions.get(task.id, [])
        
        first_iteration = common.first(iterations)
        last_iteration = common.last(iterations)
        last_execution = common.last(executions)
        
        last_execution_time = last_execution.executed_time if last_execution else None
        if not last_execution_time:
            last_iteration = common.last(iterations)
            last_execution_time = last_iteration.timestamp if last_iteration else None
        
        task.first_iteration_time = first_iteration.timestamp if first_iteration else None
        task.last_iteration_time = last_iteration.timestamp if last_iteration else None
        task.last_execution_time = last_execution_time
    
    return {"tasks": tasks}


def _initiative_tab_available_infrastructure_stacks(_: Initiative) -> bool:
    return True


def _initiative_tab_context_infrastructure_stacks(initiative: Initiative) -> dict:
    stacks_qs = initiative.cloudformation_stacks.all().order_by("aws_env", "stack_type", "created_timestamp")
    stack_entries: list[dict] = []
    default_region = AwsEnv.DEV.get_aws_region()
    
    for stack in stacks_qs:
        stack_type_enum = InfrastructureStackType.valid_or(getattr(stack, "stack_type", None), None)
        env_enum = AwsEnv.valid_or(getattr(stack, "aws_env", None), None)
        region = env_enum.get_aws_region() if env_enum else default_region
        
        cloudformation_url = None
        if stack.stack_arn:
            cloudformation_url = (
                f"https://console.aws.amazon.com/cloudformation/home#/stacks/stackinfo?stackId={quote(stack.stack_arn, safe='')}"
            )
        elif stack.stack_name:
            stack_name_encoded = quote(stack.stack_name, safe='')
            cloudformation_url = (
                f"https://{region}.console.aws.amazon.com/cloudformation/home"
                f"?region={region}#stacks?filteringStatus=active&filteringText={stack_name_encoded}"
            )
        
        logs_url = (
            f"https://{region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={region}#logsV2:log-groups$3FlogGroupNameFilter$3D{quote(stack.stack_namespace_token, safe='')}"
        )
        
        stack_entries.append(
            {
                "id": str(stack.id),
                "stack_name": stack.stack_name,
                "stack_type_label": stack_type_enum.label() if stack_type_enum else (stack.stack_type or "Unknown"),
                "stack_type_value": stack.stack_type,
                "aws_env_label": env_enum.label() if env_enum else (stack.aws_env or "Unknown"),
                "aws_env_value": stack.aws_env,
                "cloudformation_url": cloudformation_url,
                "cloudwatch_logs_url": logs_url,
                "stack_namespace_token": stack.stack_namespace_token,
                "stack_arn": stack.stack_arn,
                "created_timestamp": stack.created_timestamp,
                "updated_timestamp": stack.updated_timestamp,
                "scope_label": "Initiative" if stack.initiative_id == initiative.id else "Business",
            }
        )
    
    return {
        "stack_entries": stack_entries,
        "child_task_count": initiative.tasks.count(),
    }


def _initiative_tab_available_processes(initiative: Initiative) -> bool:
    return False  # RunningProcess.objects.filter(task_execution__task__initiative=initiative).exists()


def _initiative_tab_context_processes(initiative: Initiative) -> dict:
    running_processes_qs = RunningProcess.objects.filter(
        task_execution__task__initiative=initiative
    ).order_by('-started_at')
    running_processes = list(running_processes_qs)
    return {
        "running_processes": running_processes,
        "running_processes_count": sum(1 for process in running_processes if process.is_running)
    }


def _initiative_tab_available_llmrequests(initiative: Initiative) -> bool:
    return initiative.llmrequest_set.exists()


def _initiative_tab_context_llmrequests(initiative: Initiative) -> dict:
    return {
        "llm_requests": initiative.llmrequest_set.order_by("-timestamp")
    }


def _initiative_tab_available_llm_spend(initiative: Initiative) -> bool:
    return initiative.llmrequest_set.exists()


def _initiative_tab_context_llm_spend(initiative: Initiative, request=None) -> dict:
    return _build_llm_spend_context(initiative=initiative, request=request)


def _initiative_tab_available_edit(initiative: Initiative) -> bool:
    return True


def _initiative_tab_context_edit(initiative: Initiative) -> dict:
    return {}


def _build_initiative_tabs(initiative: Initiative) -> list[dict]:
    from erieiron_ui import tab_defitions
    
    tabs: list[dict] = []
    for definition in tab_defitions.INITIATIVE_TAB_DEFINITIONS:
        if definition.get("is_divider"):
            tabs.append(definition)
            continue
        
        slug = definition["slug"]
        available = definition["availability_fn"](initiative)
        if slug == "overview":
            url = reverse('view_initiative', args=[initiative.id])
        else:
            url = reverse('view_initiative_tab', args=[slug, initiative.id])
        
        tab_entry = {
            **definition,
            "url": url,
            "available": available,
        }
        
        if slug == "overview":
            tab_entry["label"] = initiative.title
        
        tabs.append(tab_entry)
    
    return tabs


def view_initiative(request, initiative_id, tab='overview'):
    from erieiron_ui import tab_defitions
    
    initiative = get_object_or_404(Initiative, pk=initiative_id)
    business = initiative.business
    
    tab_slug = (tab or 'overview').lower()
    if tab_slug not in tab_defitions.INITIATIVE_TAB_MAP:
        raise Http404
    
    tabs = _build_initiative_tabs(initiative)
    tab_definition = tab_defitions.INITIATIVE_TAB_MAP[tab_slug]
    
    active_tab_entry = next((t for t in tabs if t.get('slug') == tab_slug), None)
    if not active_tab_entry or not active_tab_entry.get('available'):
        raise Http404
    
    context = {
        "initiative": initiative,
        "business": business,
        "tabs": tabs,
        "active_tab": tab_slug,
        "tab_template": tab_definition["template"],
    }
    if tab_slug == 'llm-spend':
        context.update(_initiative_tab_context_llm_spend(initiative, request=request))
    else:
        context.update(tab_definition["context_fn"](initiative))
    
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name),
        (reverse('view_business', args=[business.id]), business.name),
        (reverse('view_initiative', args=[initiative.id]), initiative.title)
    ]
    if tab_slug != 'overview':
        breadcrumbs.append((reverse('view_initiative_tab', args=[tab_slug, initiative.id]), tab_definition["label"]))
    
    return send_response(
        request,
        "initiative/initiative_base.html",
        context,
        breadcrumbs=breadcrumbs
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
    iteration = self_driving_task.selfdrivingtaskiteration_set.order_by("timestamp").first()
    
    return {"iteration": iteration}


def _task_tab_context_latest_iteration_logs(task, business, self_driving_task) -> dict:
    if not self_driving_task:
        return {"iteration": None}
    
    iteration = self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp").first()
    return {"iteration": iteration}


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


def _task_tab_available_codefiles(task, business, self_driving_task) -> bool:
    if not self_driving_task:
        return False
    
    return CodeVersion.objects.filter(
        task_iteration__self_driving_task=self_driving_task
    ).exists()


def _task_tab_context_codefiles(task, business, self_driving_task) -> dict:
    if not self_driving_task:
        return {"code_file_tree": []}
    
    code_versions = list(
        CodeVersion.objects
        .filter(task_iteration__self_driving_task=self_driving_task)
        .select_related("code_file", "task_iteration")
        .order_by("code_file__file_path", "task_iteration__timestamp", "id")
    )
    
    if not code_versions:
        return {"code_file_tree": []}
    
    file_entries: dict[str, dict] = {}
    for code_version in code_versions:
        code_file = code_version.code_file
        iteration = code_version.task_iteration
        if not code_file or not iteration:
            continue
        
        file_entry = file_entries.setdefault(
            code_file.file_path,
            {
                "path": code_file.file_path,
                "codefile_id": code_file.id,
                "iterations": OrderedDict(),
            }
        )
        
        file_entry["iterations"][iteration.id] = {
            "id": iteration.id,
            "version_number": iteration.version_number,
            "timestamp": iteration.timestamp,
        }
    
    if not file_entries:
        return {"code_file_tree": []}
    
    tree_root = {
        "name": "",
        "path": "",
        "type": "dir",
        "children": OrderedDict(),
    }
    
    for file_path, entry in sorted(file_entries.items()):
        parts = [part for part in Path(file_path).parts if part]
        if not parts:
            continue
        
        node = tree_root
        ancestry: list[str] = []
        for directory in parts[:-1]:
            ancestry.append(directory)
            children = node.setdefault("children", OrderedDict())
            if directory not in children:
                children[directory] = {
                    "name": directory,
                    "path": "/".join(ancestry),
                    "type": "dir",
                    "children": OrderedDict(),
                }
            node = children[directory]
        
        iterations = list(entry["iterations"].values())
        iterations.sort(key=lambda item: item["version_number"])
        
        node.setdefault("children", OrderedDict())[parts[-1]] = {
            "name": parts[-1],
            "path": file_path,
            "type": "file",
            "codefile_id": entry["codefile_id"],
            "iterations": iterations,
        }
    
    def _ordered_children(node: dict) -> dict:
        children = node.get("children", OrderedDict())
        ordered_children = []
        for child_name, child_node in sorted(
                children.items(),
                key=lambda item: (0 if item[1]["type"] == "dir" else 1, item[0])
        ):
            ordered_children.append(_ordered_children(child_node))
        node["children"] = ordered_children
        return node
    
    ordered_tree = _ordered_children(tree_root)
    
    return {"code_file_tree": ordered_tree["children"]}


def _task_tab_available_executions(task, business, self_driving_task) -> bool:
    return False  # task.taskexecution_set.exists()


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


def _task_tab_context_edit(task, business, self_driving_task: SelfDrivingTask) -> dict:
    sandbox_path = self_driving_task.sandbox_path if self_driving_task else ""
    
    if self_driving_task:
        initiative = self_driving_task.task.initiative
        stack = initiative.cloudformation_stacks.filter(aws_env=AwsEnv.DEV, stack_type=InfrastructureStackType.APPLICATION).first()
        if stack:
            cloudformation_stack_name = stack.stack_name
            if stack.stack_arn:
                encoded_stack_id = quote(stack.stack_arn, safe="")
                cloudformation_stack_url = f"https://console.aws.amazon.com/cloudformation/home#/stacks/stackinfo?stackId={encoded_stack_id}"
    
    return {
        "sandbox_path": sandbox_path,
        "task_status_choices": TaskStatus.choices(),
        "task_execution_schedule_choices": TaskExecutionSchedule.choices(),
        "task_type_choices": TaskType.choices(),
        "task_role_choices": Role.choices() if hasattr(Role, 'choices') else [],
        "task_phase_choices": [],
    }


def _task_tab_available_llm_spend(task: Task, business: Business, self_driving_task) -> bool:
    return LlmRequest.objects.filter(task_iteration__self_driving_task__task=task).exists()


def _task_tab_context_llm_spend(task: Task, business: Business, self_driving_task, request=None) -> dict:
    initiative = task.initiative
    return _build_llm_spend_context(
        business=business,
        initiative=initiative,
        task=task,
        request=request,
        enable_range_selection=False,
    )


def _build_task_tabs(task, business, self_driving_task):
    from erieiron_ui import tab_defitions
    
    tabs = []
    for definition in tab_defitions.TASK_TAB_DEFINITIONS:
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


def _iteration_tab_available_routing(iteration: SelfDrivingTaskIteration, **_):
    return True


def _iteration_tab_context_routing(iteration: SelfDrivingTaskIteration, **_):
    active_llm_calls = iteration.llmrequest_set.filter(response__isnull=True).order_by("timestamp")
    return {
        "all_llm_requests": iteration.llmrequest_set.all().order_by("timestamp"),
        "active_llm_calls": active_llm_calls
    }


def _iteration_tab_available_planning(iteration: SelfDrivingTaskIteration, **_):
    return True  # bool(getattr(iteration, "planning_json", None))


def _iteration_tab_context_planning(iteration: SelfDrivingTaskIteration, **_):
    code_file_datas = common.ensure_list(common.get(iteration, ["planning_json", "code_files"]))
    code_file_paths = [
        code_file.get('code_file_path')
        for code_file in code_file_datas
    ]
    
    code_file_map = {
        cf.file_path: cf
        for cf in iteration.self_driving_task.business.codefile_set.filter(file_path__in=code_file_paths)
    }

    code_files = []
    for code_file in code_file_datas:
        code_file_path = code_file.get('code_file_path')
        if code_file_path and code_file_path in code_file_map:
            codefile_id = code_file_map[code_file_path].id
            code_file['url'] = reverse(view_codefile, args=[codefile_id])
            
        code_files.append(code_file)
    
    return {
        "iteration": iteration,
        "code_files": code_files
    }


def _iteration_tab_available_evaluation(iteration: SelfDrivingTaskIteration, **_):
    return bool(getattr(iteration, "evaluation_json", None))


def _iteration_tab_context_evaluation(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_available_cloudformation_logs(iteration: SelfDrivingTaskIteration, **_):
    return bool(iteration.cloudformation_logs)


def _iteration_tab_available_codelog(iteration: SelfDrivingTaskIteration, **_):
    return getattr(iteration, "log_content_coding") or getattr(iteration, "log_content_execution")


def _iteration_tab_context_cloudformation_logs(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_context_codelog(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_available_execlog(iteration: SelfDrivingTaskIteration, **_):
    return bool(getattr(iteration, "log_content_execution", None))


def _iteration_tab_context_execlog(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_available_processes(iteration: SelfDrivingTaskIteration, running_processes=None, **_):
    # if running_processes is None:
    #     return False
    # return bool(running_processes)
    return False


def _iteration_tab_context_processes(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_available_llmrequests(iteration: SelfDrivingTaskIteration, llm_requests=None, **_):
    if llm_requests is None:
        return False
    return bool(llm_requests)


def _iteration_tab_context_llmrequests(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_available_tools(iteration: SelfDrivingTaskIteration, **_):
    return True


def _iteration_tab_context_tools(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _build_iteration_tabs(
        iteration: SelfDrivingTaskIteration,
        task: Task,
        previous_iteration: SelfDrivingTaskIteration | None,
        next_iteration: SelfDrivingTaskIteration | None,
        running_processes,
        running_processes_count: int,
        llm_requests,
        active_tab_slug: str | None = None,
):
    from erieiron_ui import tab_defitions
    
    if not iteration:
        return []
    
    tabs = []
    current_tab_slug = (active_tab_slug or "routing").lower()
    
    for definition in tab_defitions.ITERATION_TAB_DEFINITIONS:
        if definition.get("is_divider"):
            tabs.append(definition)
            continue
        
        slug = definition["slug"]
        available = definition["availability_fn"](
            iteration,
            task=task,
            previous_iteration=previous_iteration,
            next_iteration=next_iteration,
            running_processes=running_processes,
            running_processes_count=running_processes_count,
            llm_requests=llm_requests,
        )
        
        if slug == "routing":
            url = reverse('view_self_driver_iteration', args=[iteration.id])
        else:
            url = reverse('view_self_driver_iteration_tab', args=[slug, iteration.id])
        
        tab_data = {**definition, "url": url, "available": available}
        
        if slug == "routing":
            tab_data["label"] = f"Iteration {iteration.version_number}"
        
        if slug == "processes" and running_processes_count:
            tab_data["badge"] = running_processes_count
        
        tabs.append(tab_data)
    
    from erieiron_ui.tab_defitions import TAB_DIVIDER
    nav_links = [TAB_DIVIDER]
    
    def _iteration_nav_url(target_iteration: SelfDrivingTaskIteration | None) -> str | None:
        if not target_iteration:
            return None
        
        slug_for_url = current_tab_slug
        if current_tab_slug != "routing":
            tab_definition = tab_defitions.ITERATION_TAB_MAP.get(current_tab_slug)
            if not tab_definition:
                slug_for_url = "routing"
            else:
                try:
                    available = tab_definition["availability_fn"](
                        target_iteration,
                        task=task,
                        previous_iteration=target_iteration.get_previous_iteration(),
                        next_iteration=target_iteration.get_next_iteration(),
                        running_processes=None,
                        running_processes_count=0,
                        llm_requests=list(target_iteration.llmrequest_set.order_by("-timestamp")),
                    )
                except Exception:
                    available = False
                if not available:
                    slug_for_url = "routing"
        
        if slug_for_url == "routing":
            return reverse('view_self_driver_iteration', args=[target_iteration.id])
        return reverse('view_self_driver_iteration_tab', args=[slug_for_url, target_iteration.id])
    
    latest_iteration = None
    try:
        latest_iteration = iteration.self_driving_task.get_most_recent_iteration()
    except Exception:
        latest_iteration = None
    
    first_iteration = None
    try:
        first_iteration = iteration.self_driving_task.selfdrivingtaskiteration_set.order_by("timestamp").first()
    except Exception:
        first_iteration = None
    
    first_url = _iteration_nav_url(first_iteration)
    latest_url = reverse('view_self_driver_latest_iteration', args=[iteration.self_driving_task.task_id])
    next_url = _iteration_nav_url(next_iteration)
    previous_url = _iteration_nav_url(previous_iteration)
    
    nav_links.append({
        "slug": "first-iteration-link",
        "label": "First Iteration",
        "url": first_url,
        "available": bool(first_url)
    })
    
    nav_links.append({
        "slug": "latest-iteration-link",
        "label": "Latest Iteration",
        "url": latest_url,
        "available": bool(latest_url)
    })
    
    nav_links.append(TAB_DIVIDER)
    
    nav_links.append({
        "slug": "previous-iteration-link",
        "label": "Previous Iteration",
        "url": previous_url,
        "available": bool(previous_url),
    })
    
    nav_links.append({
        "slug": "next-iteration-link",
        "label": "Next Iteration",
        "url": next_url,
        "available": bool(next_url)
    })
    
    first_divider_index = next(
        (idx for idx, tab in enumerate(tabs) if tab.get("is_divider")),
        len(tabs)
    )
    if len(nav_links) > 1:
        tabs[first_divider_index:first_divider_index] = nav_links
    
    return tabs


def view_task(request, task_id, tab='overview'):
    from erieiron_ui import tab_defitions
    
    task = get_object_or_404(Task, pk=task_id)
    initiative = task.initiative
    business = initiative.business
    
    tab_slug = (tab or 'overview').lower()
    if tab_slug not in tab_defitions.TASK_TAB_MAP:
        raise Http404
    
    self_driving_task = SelfDrivingTask.objects.filter(task_id=task.id).first()
    tabs = _build_task_tabs(task, business, self_driving_task)
    tab_definition = tab_defitions.TASK_TAB_MAP[tab_slug]
    
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
    if tab_slug == 'llm-spend':
        context.update(_task_tab_context_llm_spend(task, business, self_driving_task, request=request))
    else:
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


def view_self_driver_iteration(request, iteration_id, tab='routing'):
    from erieiron_ui import tab_defitions
    
    iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)
    
    self_driving_task = iteration.self_driving_task
    self_driving_task = self_driving_task
    task = self_driving_task.task
    initiative = task.initiative
    business = initiative.business
    
    total_price, total_tokens = iteration.get_llm_cost()
    
    running_processes_qs = RunningProcess.objects.filter(
        task_execution__iteration=iteration
    ).order_by("-started_at")
    running_processes = list(running_processes_qs)
    running_processes_count = RunningProcess.objects.filter(
        task_execution__iteration=iteration,
        is_running=True
    ).count()
    
    previous_evaluations = []
    
    _, iteration_to_modify = iteration.get_relevant_iterations()
    previous_iteration: SelfDrivingTaskIteration | None = iteration.get_previous_iteration()
    next_iteration: SelfDrivingTaskIteration | None = iteration.get_next_iteration()
    
    try:
        last_iteration: SelfDrivingTaskIteration | None = self_driving_task.get_most_recent_iteration()
    except Exception:
        last_iteration = None
    
    llm_requests = list(iteration.llmrequest_set.order_by("-timestamp"))
    
    tab_slug = (tab or 'routing').lower()
    if tab_slug not in tab_defitions.ITERATION_TAB_MAP:
        raise Http404
    
    tabs = _build_iteration_tabs(
        iteration=iteration,
        task=task,
        previous_iteration=previous_iteration,
        next_iteration=next_iteration,
        running_processes=running_processes,
        running_processes_count=running_processes_count,
        llm_requests=llm_requests,
        active_tab_slug=tab_slug,
    )
    
    tab_entry = next((t for t in tabs if t.get('slug') == tab_slug), None)
    if not tab_entry or not tab_entry.get('available'):
        raise Http404
    
    tab_definition = tab_defitions.ITERATION_TAB_MAP[tab_slug]
    tab_context = tab_definition["context_fn"](
        iteration,
        task=task,
        previous_iteration=previous_iteration,
        next_iteration=next_iteration,
        running_processes=running_processes,
        running_processes_count=running_processes_count,
        llm_requests=llm_requests,
    )
    
    context = {
        "iteration": iteration,
        "previous_iteration": previous_iteration,
        "iteration_to_modify": iteration_to_modify,
        "next_iteration": next_iteration,
        "last_iteration": last_iteration,
        "previous_evaluations": previous_evaluations,
        "running_processes_count": running_processes_count,
        "task": task,
        "self_driving_task": self_driving_task,
        "initiative": initiative,
        "business": business,
        "llm_requests": llm_requests,
        "total_price": total_price,
        "total_tokens": total_tokens,
        "running_processes": running_processes,
        "tabs": tabs,
        "active_tab": tab_slug,
        "tab_template": tab_definition["template"],
    }
    context.update(tab_context)
    
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name),
        (reverse(view_business, args=[business.id]), business.name),
        (reverse(view_initiative, args=[initiative.id]), initiative.title),
        (reverse(view_task, args=[task.id]), task.get_name()),
        (reverse('view_self_driver_iteration', args=[iteration.id]), f"Iteration {iteration.version_number}"),
    ]
    if tab_slug != 'routing':
        breadcrumbs.append((
            reverse('view_self_driver_iteration_tab', args=[tab_slug, iteration.id]),
            tab_definition["label"]
        ))
    
    return send_response(
        request,
        "iteration/iteration_base.html",
        context,
        breadcrumbs=breadcrumbs
    )


@json_endpoint
def view_iteration_logs(request, iteration_id):
    iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)
    
    log_content_coding = getattr(iteration, "log_content_coding", None) or ""
    log_content_execution = getattr(iteration, "log_content_execution", None) or ""
    
    return {
        "log_text": f"{log_content_coding}{log_content_execution}",
    }


@json_endpoint
def view_task_latest_iteration_logs(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    self_driving_task = getattr(task, "selfdrivingtask", None)
    
    if not self_driving_task:
        return {
            "log_text": "",
            "iteration_id": None,
        }
    
    iteration = self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp").first()
    if not iteration:
        return {
            "log_text": "",
            "iteration_id": None,
        }
    
    log_content_coding = getattr(iteration, "log_content_coding", None) or ""
    log_content_execution = getattr(iteration, "log_content_execution", None) or ""
    
    timestamp = getattr(iteration, "timestamp", None)
    timestamp_display = None
    if timestamp:
        try:
            localized = timezone.localtime(timestamp)
        except (ValueError, TypeError, AttributeError):
            localized = timestamp
        timestamp_display = formats.date_format(localized, "DATETIME_FORMAT")
    
    return {
        "log_text": f"{log_content_coding}{log_content_execution}",
        "iteration_id": str(iteration.id),
        "iteration_version_number": iteration.version_number,
        "iteration_timestamp": timestamp.isoformat() if timestamp else None,
        "iteration_timestamp_display": timestamp_display,
    }


@json_endpoint
def view_task_phase_state(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    self_driving_task = getattr(task, "selfdrivingtask", None)
    if not self_driving_task:
        return {
            "task_id": task.id,
            "phase_change_seq": None,
            "latest_phase_change_at": None,
            "iteration_id": None,
            "iteration_version_number": None,
        }
    try:
        latest_iteration = self_driving_task.get_most_recent_iteration()
    except Exception:
        latest_iteration = None
    return {
        "task_id": task.id,
        "phase_change_seq": self_driving_task.phase_change_seq,
        "latest_phase_change_at": self_driving_task.latest_phase_change_at,
        "iteration_id": getattr(latest_iteration, "id", None),
        "iteration_version_number": getattr(latest_iteration, "version_number", None),
    }


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
    
    for stack in InfrastructureStack.objects.filter(initiative_id=task.initiative_id):
        stack.tombstone()
    
    # Reset task status and clear any existing executions
    Task.objects.filter(id=task_id).update(
        status=TaskStatus.NOT_STARTED
    )
    
    if SelfDrivingTask.objects.filter(task_id=task.id).exists():
        SelfDrivingTask.objects.filter(id=task.selfdrivingtask.id).update(
            test_file_path=None,
            initial_tests_pass=False
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
    operation_type = rget(request, 'operation_type', BusinessOperationType.ERIE_IRON_AUTONOMOUS.value)
    operation_type = (operation_type or '').strip() or BusinessOperationType.ERIE_IRON_AUTONOMOUS.value
    
    if not business_name:
        messages.error(request, 'Business name is required.')
        return redirect(reverse('view_businesses_tab', args=['tools']))
    
    if not business_description:
        messages.error(request, 'Business description is required.')
        return redirect(reverse('view_businesses_tab', args=['tools']))
    
    if not BusinessOperationType.valid(operation_type):
        messages.error(request, 'Invalid operation type selected.')
        return redirect(reverse('view_businesses_tab', args=['tools']))
    
    business = Business.objects.create(
        name=business_name,
        source=BusinessIdeaSource.HUMAN,
        raw_idea=business_description,
        operation_type=operation_type
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
    
    return redirect(reverse('view_initiative_tab', args=['architecture', initiative_id]))


def action_initiative_regenerate_tasks(request, initiative_id):
    initiative: Initiative = get_object_or_404(Initiative, pk=initiative_id)
    
    initiative.tasks.all().delete()
    eng_lead.define_tasks_for_initiative(initiative_id)
    
    return redirect(reverse('view_initiative_tab', args=['tasks', initiative_id]))


@require_POST
def action_delete_business(request, business_id):
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
        return redirect(reverse('view_businesses_tab', args=['tools']))
    
    if not initiative_type:
        messages.error(request, 'Initiative type is required.')
        return redirect(reverse('view_businesses_tab', args=['tools']))
    
    if not description:
        messages.error(request, 'Initiative description is required.')
        return redirect(reverse('view_businesses_tab', args=['tools']))
    
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
    return redirect(reverse('view_initiative', args=[initiative.id]))


def action_dowork_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()
    
    try:
        initiative = get_object_or_404(Initiative, id=initiative_id)
        initiative_title = initiative.title
        
        for t in initiative.tasks.exclude(status=TaskStatus.COMPLETE).order_by("created_timestamp"):
            PubSubManager.publish_id(PubSubMessageType.TASK_UPDATED, t.id)
            break
        
        messages.success(request, f'Work successfully kicked off on "{initiative_title}"')
    except Initiative.DoesNotExist:
        messages.error(request, 'Initiative not found.')
    except Exception as e:
        messages.error(request, f'Error with initiative: {str(e)}')
    
    return redirect(reverse('view_initiative_tab', args=['tasks', initiative_id]))


def action_update_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()
    
    try:
        initiative = get_object_or_404(Initiative, pk=initiative_id)
        
        # Get form data
        title = rget(request, 'title', '').strip()
        description = rget(request, 'description', '').strip()
        architecture = rget(request, 'architecture', '').strip()
        cloudformation_stack_name = rget(request, 'cloudformation_stack_name', '').strip()
        requires_unit_tests = request.POST.get('requires_unit_tests') == 'on'
        green_lit = request.POST.get('green_lit') == 'on'
        
        # Check if green_lit status is changing from False to True
        was_green_lit = initiative.green_lit
        
        # Prepare update data
        update_data = {
            'title': title,
            'description': description,
            'architecture': architecture or None,
            'cloudformation_stack_name': cloudformation_stack_name or None,
            'requires_unit_tests': requires_unit_tests,
            'green_lit': green_lit
        }
        
        # Update the initiative
        Initiative.objects.filter(id=initiative_id).update(**update_data)
        
        # Publish INITIATIVE_GREEN_LIT event if green_lit changed from False to True
        if not was_green_lit and green_lit:
            PubSubManager.publish_id(PubSubMessageType.INITIATIVE_GREEN_LIT, initiative_id)
        
        messages.success(request, 'Initiative updated successfully!')
        return redirect(reverse('view_initiative_tab', args=['edit', initiative_id]))
    except Initiative.DoesNotExist:
        messages.error(request, 'Initiative not found.')
        return redirect(reverse('view_businesses_tab', args=['initiatives']))
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
    
    return redirect(reverse('view_businesses_tab', args=['initiatives']))


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
    
    return redirect(reverse('view_businesses_tab', args=['portfolio']))


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
        timeout_seconds = rget(request, 'timeout_seconds', '').strip()
        max_budget_usd = rget(request, 'max_budget_usd', '').strip()
        status = rget(request, 'status', '').strip()
        task_type = rget(request, 'task_type', '').strip()
        execution_schedule = rget(request, 'execution_schedule', '').strip()
        execution_start_time = rget(request, 'execution_start_time', '').strip()
        cloudformation_stack_name = rget(request, 'cloudformation_stack_name', '').strip()
        completion_criteria = rget_json(request, 'completion_criteria', ["all tests pass"])
        requires_test = request.POST.get('requires_test') == 'on'
        
        # Prepare update data
        update_data = {
            'description': description,
            'completion_criteria': completion_criteria,
            'risk_notes': risk_notes,
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
        logging.exception(e)
        messages.error(request, 'Task not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        logging.exception(e)
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
        web_container_cpu = max(1, rget_int(request, 'web_container_cpu', business.web_container_cpu or 512))
        web_container_memory = max(1, rget_int(request, 'web_container_memory', business.web_container_memory or 1024))
        web_desired_count = max(1, rget_int(request, 'web_desired_count', business.web_desired_count or 1))
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
            'web_container_cpu': web_container_cpu,
            'web_container_memory': web_container_memory,
            'web_desired_count': web_desired_count,
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


def action_business_new_domain(request, business_id):
    if request.method != 'POST':
        raise Exception()
    
    business = get_object_or_404(Business, pk=business_id)
    business.needs_domain = True
    business.domain = None
    business.save(update_fields=["domain", "needs_domain"])
    
    domain_manager.manage_domain(business)
    
    return redirect(reverse('view_business_tab', args=['edit', business_id]))


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
            redirect_url = reverse('view_self_driver_iteration_tab', args=['processes', running_process.task_execution.iteration_id])
        elif running_process.task_execution.task:
            task_id = running_process.task_execution.task.id
            redirect_url = reverse('view_task_tab', args=['processes', task_id])
        else:
            redirect_url = reverse('view_businesses_tab', args=['tools'])
        
        if running_process.kill_process():
            messages.success(request, f'Process {process_id} killed successfully!')
        else:
            messages.warning(request, f'Failed to kill process {process_id} - it may have already terminated.')
        
        return redirect(redirect_url)
    except RunningProcess.DoesNotExist:
        messages.error(request, 'Process not found.')
        return redirect(reverse('view_businesses_tab', args=['tools']))
    except Exception as e:
        messages.error(request, f'Error killing process: {str(e)}')
        return redirect(reverse('view_businesses_tab', args=['tools']))


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


def action_rollback_iteration(request, iteration_id):
    if request.method != 'POST':
        raise Exception()
    
    try:
        iteration = get_object_or_404(SelfDrivingTaskIteration, pk=iteration_id)
        task = iteration.self_driving_task
        
        newer_iterations_qs = task.selfdrivingtaskiteration_set.filter(timestamp__gt=iteration.timestamp)
        newer_iterations_count = newer_iterations_qs.count()
        if newer_iterations_count:
            newer_iterations_qs.delete()
        
        iteration.write_to_disk()
        
        iteration_display = iteration.version_number if iteration.version_number is not None else iteration.id
        if newer_iterations_count:
            plural_suffix = '' if newer_iterations_count == 1 else 's'
            messages.success(
                request,
                f'Rolled back to iteration {iteration_display}. Deleted {newer_iterations_count} newer iteration{plural_suffix}.'
            )
        else:
            messages.success(request, f'Rolled back to iteration {iteration_display}. No newer iterations were removed.')
        
        return redirect(reverse('view_self_driver_iteration', args=[iteration.id]))
    except SelfDrivingTaskIteration.DoesNotExist:
        messages.error(request, 'Iteration not found.')
        return redirect(reverse('view_businesses'))
    except Exception as e:
        messages.error(request, f'Error rolling back iteration: {str(e)}')
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
        return redirect(reverse('view_businesses_tab', args=['lessons']))
    except AgentLesson.DoesNotExist:
        messages.error(request, 'Lesson not found.')
        return redirect(reverse('view_businesses_tab', args=['lessons']))
    except Exception as e:
        messages.error(request, f'Error updating lesson: {str(e)}')
        return redirect(reverse('view_businesses_tab', args=['lessons']))


def view_codefile(request, codefile_id):
    code_file = get_object_or_404(CodeFile, pk=codefile_id)
    business = code_file.business
    
    # Get all versions of this file in chronological order (newest first)
    code_versions = list(
        CodeVersion.objects
        .filter(code_file=code_file)
        .select_related("task_iteration", "task_iteration__self_driving_task", "task_iteration__self_driving_task__task")
        .order_by("-created_at")
    )
    
    # Use business view context with tabs
    tabs = _build_business_tabs(business)
    
    context = {
        "business": business,
        "tabs": tabs,
        "active_tab": "codefiles",  # Create a virtual tab for codefiles
        "tab_template": "codefile.html",
        "code_file": code_file,
        "code_versions": code_versions,
    }
    
    # Build breadcrumbs 
    breadcrumbs = [
        (reverse(view_businesses), Business.get_erie_iron_business().name),
        (reverse('view_business', args=[business.id]), business.name),
        (reverse('view_codefile', args=[codefile_id]), code_file.file_path)
    ]
    
    if code_versions:
        most_recent_version = code_versions[0]
        if most_recent_version.task_iteration:
            task_iteration = most_recent_version.task_iteration
            self_driving_task = task_iteration.self_driving_task
            if self_driving_task and self_driving_task.task:
                task = self_driving_task.task
                initiative = task.initiative
    
    return send_response(
        request,
        "business/business_base.html",
        context,
        breadcrumbs=breadcrumbs
    )


@json_endpoint
def api_codefile_content(request, codefile_id):
    """API endpoint for rendering codefile content based on selected versions."""
    code_file = get_object_or_404(CodeFile, pk=codefile_id)
    
    # Get selected version IDs from request
    version_ids = rget_list(request, 'versions')
    version_ids = common.filter_empty(version_ids)
    
    # Convert to integers and filter valid versions
    try:
        code_versions = list(
            CodeVersion.objects
            .filter(code_file=code_file, id__in=version_ids)
            .select_related("task_iteration")
            .order_by("-created_at")
        )
    except (ValueError, TypeError):
        return {"error": "Invalid version IDs provided"}
    
    if not version_ids:
        # No versions selected - show latest version
        latest_version = (
            CodeVersion.objects
            .filter(code_file=code_file)
            .select_related("task_iteration")
            .order_by("-created_at")
            .first()
        )
        if latest_version:
            return {
                "title": "Latest Version",
                "content": latest_version.code,
                "content_type": "code"
            }
        else:
            return {"title": "No Versions", "content": "No code versions found.", "content_type": "message"}
    
    elif len(version_ids) == 1:
        # Single version selected - show that version's code
        version = code_versions[0] if code_versions else None
        if version:
            title = f"Version {version.task_iteration.version_number if version.task_iteration else version.id}"
            return {
                "title": title,
                "content": version.code,
                "content_type": "code"
            }
        else:
            return {"error": "Selected version not found"}
    
    elif len(version_ids) == 2:
        # Two versions - show diff between them
        if len(code_versions) == 2:
            # Sort by iteration number for proper diff order
            sorted_versions = sorted(code_versions, key=lambda v: v.task_iteration.version_number if v.task_iteration else 0)
            old_version, new_version = sorted_versions
            
            diff_html = _generate_diff_html(old_version, new_version)
            title = f"Diff: v{old_version.task_iteration.version_number if old_version.task_iteration else old_version.id} → v{new_version.task_iteration.version_number if new_version.task_iteration else new_version.id}"
            
            return {
                "title": title,
                "content": diff_html,
                "content_type": "diff"
            }
        else:
            return {"error": "Could not find both selected versions"}
    
    else:
        # Multiple versions - show sequential diffs
        if len(code_versions) >= 2:
            # Sort by iteration number
            sorted_versions = sorted(code_versions, key=lambda v: v.task_iteration.version_number if v.task_iteration else 0)
            
            diffs_html = []
            for i in range(len(sorted_versions) - 1):
                old_version = sorted_versions[i]
                new_version = sorted_versions[i + 1]
                
                diff_title = f"v{old_version.task_iteration.version_number if old_version.task_iteration else old_version.id} → v{new_version.task_iteration.version_number if new_version.task_iteration else new_version.id}"
                diff_html = _generate_diff_html(old_version, new_version)
                
                diffs_html.append(f'<div class="sequential-diff"><h6 class="diff-title">{diff_title}</h6>{diff_html}</div>')
            
            title = f"Sequential Diffs ({len(sorted_versions)} versions)"
            return {
                "title": title,
                "content": ''.join(reversed(diffs_html)),
                "content_type": "diff"
            }
        else:
            return {"error": "Not enough versions found for comparison"}


def _generate_diff_html(old_version, new_version):
    """Generate HTML diff between two code versions."""
    old_lines = old_version.code.splitlines(keepends=True)
    new_lines = new_version.code.splitlines(keepends=True)
    
    # Generate unified diff
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"Version {old_version.task_iteration.version_number if old_version.task_iteration else old_version.id}",
        tofile=f"Version {new_version.task_iteration.version_number if new_version.task_iteration else new_version.id}",
        lineterm='',
        n=3
    )
    
    # Convert to HTML with syntax highlighting
    diff_lines = []
    for line in diff:
        escaped_line = escape(line.rstrip('\n'))
        if line.startswith('+++') or line.startswith('---'):
            diff_lines.append(f'<div class="diff-header">{escaped_line[3:]}</div>')
        elif line.startswith('@@'):
            diff_lines.append(f'<div class="diff-hunk-header">{escaped_line[2:]}</div>')
        elif line.startswith('+'):
            diff_lines.append(f'<div class="diff-added">{escaped_line[1:]}</div>')
        elif line.startswith('-'):
            diff_lines.append(f'<div class="diff-removed">{escaped_line[1:]}</div>')
        else:
            diff_lines.append(f'<div class="diff-context">{escaped_line}</div>')
    
    return ''.join(diff_lines)


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
    change_prompt = rget_bool(request, "change_prompt")
    prompt = rget(request, "prompt")
    llm_model, verbosity, reasoning_effort = rget(request, "llm_model").split(";")
    
    orig_llm_request = LlmRequest.objects.get(id=llm_request_id)
    
    if optimize:
        title = "Optimize"
        system_prompt = "chat_response_interpreter.md"
        schema = None
    elif change_prompt:
        title = "Change"
        system_prompt = "llm_prompt_changer.md"
        schema = "llm_prompt_changer.md.schema.json"
    else:
        title = "Debug"
        system_prompt = "chat_evaluator.md"
        schema = None
    
    resp = system_agent_llm_interface.llm_chat(
        description=f"{title} {orig_llm_request.title}",
        messages=[
            get_sys_prompt(system_prompt),
            LlmMessage.user_from_data(
                "Chat Interatction",
                orig_llm_request.get_llm_data()
            ),
            prompt
        ],
        output_schema=schema,
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
    
    if change_prompt:
        pprint.pprint(resp.json())
        return send_response(
            request,
            "_llm_changes_response.html",
            {
                **resp.json()
            }
        )
    else:
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
    iteration = llm_request.task_iteration
    task = iteration.self_driving_task.task if iteration else None
    previous_iteration: SelfDrivingTaskIteration | None = iteration.get_previous_iteration() if iteration else None
    next_iteration: SelfDrivingTaskIteration | None = iteration.get_next_iteration() if iteration else None
    
    if llm_request.business_id:
        breadcrumbs.append(
            (reverse('view_business_tab', args=['product-initiatives', llm_request.business_id]), llm_request.business.name),
        )
        if llm_request.initiative:
            breadcrumbs.append(
                (reverse('view_initiative_tab', args=['tasks', llm_request.initiative_id]), llm_request.initiative.title),
            )
            if iteration:
                breadcrumbs.append(
                    (f"{reverse(view_task, args=[task.id])}#iterations", task.get_name())
                )
                breadcrumbs.append(
                    (
                        reverse('view_self_driver_iteration', args=[llm_request.task_iteration_id]),
                        f"Iteration {iteration.version_number}"
                    )
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
    
    llm_requests = list(iteration.llmrequest_set.order_by("-timestamp")) if iteration else []
    
    return send_response(
        request,
        "llm_request.html",
        {
            "iteration": iteration,
            "task": task,
            "initiative": llm_request.initiative,
            "business": llm_request.business,
            "tabs": _build_iteration_tabs(iteration, task, previous_iteration, next_iteration, None, None, llm_requests),
            "llm_request": llm_request,
            "model_choices": model_choices,
            "model_choice_value": f"{LlmModel.OPENAI_GPT_5_MINI.value};{LlmVerbosity.MEDIUM};{LlmReasoningEffort.MEDIUM}"
        },
        breadcrumbs=breadcrumbs
    )
