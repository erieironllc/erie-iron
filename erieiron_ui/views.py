import difflib
import inspect
import json
import logging
import pprint
import re
import uuid
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Callable, Iterable, Any
from urllib.parse import quote

import jwt
from django.contrib import messages
from django.http import HttpResponse, Http404, JsonResponse, HttpResponseRedirect, HttpRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone, formats
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST

import settings
from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent_tofu
from erieiron_autonomous_agent.enums import TaskStatus, BusinessStatus, BusinessOperationType
from erieiron_autonomous_agent.models import (
    Business,
    BusinessKPI,
    LlmRequest,
    AgentLesson,
    CodeFile,
    CodeVersion,
    InfrastructureStack,
)
from erieiron_autonomous_agent.models import Task, Initiative, SelfDrivingTask, SelfDrivingTaskIteration, TaskExecution, RunningProcess
from erieiron_autonomous_agent.system_agent_llm_interface import get_sys_prompt
from erieiron_common import common, domain_manager, ErieIronJSONEncoder
from erieiron_common.aws_utils import aws_console_url_from_arn
from erieiron_common.enums import PubSubMessageType, PubSubMessageStatus, BusinessIdeaSource, Constants, TaskExecutionSchedule, TaskType, Level, LlmModel, LlmVerbosity, LlmReasoningEffort, Role, InfrastructureStackType, EnvironmentType, InitiativeType, InitiativeNames
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import PubSubMessage
from erieiron_common.view_utils import send_response, redirect, rget, rget_bool, rget_int, json_endpoint, rget_list

logger = logging.getLogger(__name__)


def _build_simple_auth_token(email: str) -> str:
    issued_at = datetime.utcnow()
    expires_at = issued_at + timedelta(seconds=settings.SIMPLE_AUTH_TOKEN_TTL_SECONDS)
    payload = {
        "email": email,
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.SIMPLE_AUTH_JWT_SECRET, algorithm="HS256")


def _resolve_post_login_redirect(request, candidate: str | None) -> str:
    fallback = reverse("view_home")
    if not candidate:
        return fallback
    
    if url_has_allowed_host_and_scheme(candidate, {request.get_host()}, ["http", "https"]):
        return candidate
    
    if candidate.startswith("/"):
        return candidate
    
    return fallback


def _credentials_match(email: str, password: str) -> bool:
    return (
            email == settings.SIMPLE_AUTH_ALLOWED_EMAIL
            and password == settings.SIMPLE_AUTH_ALLOWED_PASSWORD
    )


def view_login(request):
    next_param = request.GET.get("next") or request.POST.get("next")
    
    if getattr(request, "simple_auth_authenticated", False):
        destination = _resolve_post_login_redirect(request, next_param)
        return HttpResponseRedirect(destination)
    
    context = {
        "page_title": "Sign in • ErieIron",
        "next": next_param or "",
        "prefill_email": request.POST.get("email", "").strip(),
    }
    
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        
        if _credentials_match(email, password):
            token = _build_simple_auth_token(email)
            destination = _resolve_post_login_redirect(request, next_param)
            response = HttpResponseRedirect(destination)
            response.set_cookie(
                settings.SIMPLE_AUTH_COOKIE_NAME,
                token,
                max_age=settings.SIMPLE_AUTH_TOKEN_TTL_SECONDS,
                httponly=True,
                secure=not settings.DEBUG,
                samesite="Lax",
            )
            return response
        
        messages.error(request, "Invalid email or password.")
    
    return render(request, "login.html", context)


def action_logout(request):
    response = HttpResponseRedirect(reverse("view_login"))
    response.delete_cookie(settings.SIMPLE_AUTH_COOKIE_NAME)
    return response


LLM_SPEND_RANGE_DEFAULT = "15d"
LLM_SPEND_RANGE_OPTIONS = [
    {"slug": "24h", "label": "Last 24 Hours"},
    {"slug": "15d", "label": "Last 15 Days"},
    {"slug": "30d", "label": "Last 30 Days"},
    {"slug": "all", "label": "All Time"},
]


def hello(request):
    return HttpResponse("hello world")


def healthcheck(request):
    logging.debug("healthcheck requested", extra={"path": request.path})
    return JsonResponse({"ok": True})




def _portfolio_tab_context_portfolio(erieiron_business: Business) -> dict:
    ei_business = Business.get_erie_iron_business()
    
    portfolio_businesses = defaultdict(list)
    for b in Business.objects.exclude(id=ei_business.id).order_by("name"):
        portfolio_businesses[BusinessStatus(b.status)].append(b)
        
    
    return {
        "operation_type_choices": BusinessOperationType.choices(),
        "operation_type_default": BusinessOperationType.ERIE_IRON_AUTONOMOUS.value,
        "businesses": [
            ei_business,
            *portfolio_businesses[BusinessStatus.ACTIVE],
            *portfolio_businesses[BusinessStatus.IDEA],
            *portfolio_businesses[BusinessStatus.PAUSED],
            *portfolio_businesses[BusinessStatus.SHUTDOWN]
        ]
    }


def _portfolio_tab_available_capacity(erieiron_business: Business) -> bool:
    return erieiron_business.businesscapacityanalysis_set.exists()


def _portfolio_tab_context_capacity(erieiron_business: Business) -> dict:
    return {
        "business_capacity_analysis_list": erieiron_business.businesscapacityanalysis_set.all().order_by("-created_timestamp"),
    }





def _portfolio_tab_available_lessons(_: Business) -> bool:
    return AgentLesson.objects.exists()


def _portfolio_tab_context_lessons(_: Business) -> dict:
    return {
        "agent_lessons": AgentLesson.objects.all().order_by("-timestamp"),
    }


def _portfolio_tab_available_tools(_: Business) -> bool:
    return True


def _portfolio_tab_context_tools(_: Business) -> dict:
    return {
        "all_running_processes": RunningProcess.objects.filter(is_running=True).order_by('-started_at'),
        "operation_type_choices": BusinessOperationType.choices(),
        "operation_type_default": BusinessOperationType.ERIE_IRON_AUTONOMOUS.value,
    }


def _portfolio_tab_available_infrastructure_stacks(_: Business) -> bool:
    return True


def _portfolio_tab_context_infrastructure_stacks(_: Business) -> dict:
    stacks_qs = (
        InfrastructureStack.objects
        .select_related("initiative", "business")
        .all()
        .order_by("env_type", "stack_type", "created_timestamp")
    )
    stacks = list(stacks_qs)

    def business_label(stack: InfrastructureStack) -> str:
        business = getattr(stack, "business", None)
        if not business:
            return "Business"
        return getattr(business, "name", None) or getattr(business, "slug", None) or "Business"

    stack_entries = _build_infrastructure_stack_entries(
        stacks,
        scope_label_fn=business_label,
    )

    business_ids = {entry.get("business_id") for entry in stack_entries if entry.get("business_id")}
    initiative_ids = {entry.get("initiative_id") for entry in stack_entries if entry.get("initiative_id")}

    for entry in stack_entries:
        business_name = entry.get("business_name") or "Business"
        initiative_title = entry.get("initiative_title")
        if initiative_title:
            entry["initiative_title"] = f"{initiative_title} ({business_name})"
        else:
            entry["scope_label"] = f"{business_name} (Business)"

    architecture_diagram = _build_infra_diagram_payload(
        stacks,
        stack_entries=stack_entries,
        diagram_namespace="portfolio-infrastructure-stacks",
        scope_label_fn=business_label,
    )

    return {
        "stack_entries": stack_entries,
        "stack_count": len(stack_entries),
        "business_count": len(business_ids),
        "initiative_count": len(initiative_ids),
        "architecture_diagram": architecture_diagram,
    }


def _portfolio_tab_available_pubsub_messages(_: Business) -> bool:
    return PubSubMessage.objects.exists()


def _portfolio_tab_context_pubsub_messages(_: Business, request: HttpRequest | None = None) -> dict:
    page_size = 20

    selected_message_types = []
    selected_statuses = []
    if request:
        selected_message_types = [value for value in request.GET.getlist("message_types") if value]
        selected_statuses = [value for value in request.GET.getlist("statuses") if value]

    qs = PubSubMessage.objects.all()
    if selected_message_types:
        qs = qs.filter(message_type__in=selected_message_types)
    if selected_statuses:
        qs = qs.filter(status__in=selected_statuses)

    qs = qs.order_by("-created_at")
    page_candidates = list(qs[:page_size + 1])
    has_more = len(page_candidates) > page_size
    pubsub_messages = page_candidates[:page_size]

    total_count = qs.count()

    raw_message_types = PubSubMessage.objects.order_by("message_type").values_list("message_type", flat=True).distinct()
    message_type_options = []
    for message_type in raw_message_types:
        if not message_type:
            continue
        if PubSubMessageType.valid(message_type):
            label = PubSubMessageType(message_type).label()
        else:
            label = message_type.replace("_", " ").title()
        message_type_options.append({
            "value": message_type,
            "label": label
        })

    message_type_options.sort(key=lambda option: option["label"].lower())

    status_options = [{
        "value": status.value,
        "label": status.label()
    } for status in PubSubMessageStatus]

    filters_applied = bool(selected_message_types or selected_statuses)

    return {
        "pubsub_messages": pubsub_messages,
        "total_count": total_count,
        "pubsub_messages_redirect": reverse('view_portfolio_tab', args=['pubsub-messages']),
        "message_type_options": message_type_options,
        "status_options": status_options,
        "selected_message_types": selected_message_types,
        "selected_statuses": selected_statuses,
        "filters_applied": filters_applied,
        "has_more": has_more,
    }


def _portfolio_tab_available_logout(_: Business) -> bool:
    return True


def _portfolio_tab_context_logout(_: Business) -> dict:
    return {}


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


def _portfolio_tab_available_llm_spend(_: Business) -> bool:
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


def _portfolio_tab_context_llm_spend(_: Business, request=None) -> dict:
    return _build_llm_spend_context(request=request)


def _tab_available_llm_spend(business: Business) -> bool:
    return business.llmrequest_set.exists()


def _tab_context_llm_spend(business: Business, request=None) -> dict:
    return _build_llm_spend_context(business=business, request=request)


def _build_portfolio_tabs(erieiron_business: Business) -> list[dict]:
    from erieiron_ui import tab_defitions
    tabs: list[dict] = []
    
    for definition in tab_defitions.PORTFOLIO_TAB_DEFINITIONS:
        if definition.get("is_divider"):
            tabs.append(definition)
            continue
        
        slug = definition["slug"]
        if "availability_fn" in definition:
            available = definition["availability_fn"](erieiron_business)
        else:
            available = True
            
        if definition.get("url_name"):
            url = reverse(definition["url_name"])
        elif definition.get("url"):
            url = definition["url"]
        elif slug == "portfolio":
            url = reverse('view_portfolio')
        else:
            url = reverse('view_portfolio_tab', args=[slug])
        
        tab_entry = {
            **definition,
            "url": url,
            "available": available,
        }
        
        tabs.append(tab_entry)
    
    return tabs


def view_portfolio(request, tab: str = 'portfolio'):
    from erieiron_ui import tab_defitions
    erieiron_business = Business.get_erie_iron_business()
    tab_slug = (tab or 'portfolio').lower()
    
    if tab_slug not in tab_defitions.PORTFOLIO_TAB_MAP:
        raise Http404
    
    tabs = _build_portfolio_tabs(erieiron_business)
    tab_definition = tab_defitions.PORTFOLIO_TAB_MAP[tab_slug]
    
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
        context.update(_portfolio_tab_context_llm_spend(erieiron_business, request=request))
    else:
        context_fn = tab_definition["context_fn"]
        fn_params = inspect.signature(context_fn).parameters
        if "request" in fn_params:
            context.update(context_fn(erieiron_business, request=request))
        else:
            context.update(context_fn(erieiron_business))
    
    breadcrumbs = [
        (reverse(view_portfolio), "Portfolio")
    ]
    if tab_slug != 'portfolio':
        breadcrumbs.append((reverse('view_portfolio_tab', args=[tab_slug]), tab_definition["label"]))
    
    return send_response(
        request,
        "portfolio/portfolio_base.html",
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


def _tab_available_architecture_diagram(_: Business) -> bool:
    return True


def _tab_context_infra_diagram(business: Business) -> dict:
    stacks = list(
        business.infrastructurestack_set
        .filter(initiative__isnull=True)
        .select_related("initiative")
        .order_by("env_type", "stack_type", "created_timestamp")
    )

    stack_entries = _build_infrastructure_stack_entries(
        stacks,
        scope_label_fn=lambda _stack: "Business",
    )

    architecture_diagram = _build_infra_diagram_payload(
        stacks,
        stack_entries=stack_entries,
        diagram_namespace=f"business-architecture-{business.id}",
        scope_label_fn=lambda _stack: "Business",
    )

    return {
        "architecture_diagram": architecture_diagram,
    }


def _tab_available_infrastructure_stacks(_: Business) -> bool:
    return True


def _tab_context_infrastructure_stacks(business: Business) -> dict:
    stacks_qs = (
        business.infrastructurestack_set
        .select_related("initiative")
        .all()
        .order_by("env_type", "stack_type", "created_timestamp")
    )
    stacks = list(stacks_qs)

    def scope_label_fn(stack: InfrastructureStack) -> str:
        if stack.initiative_id:
            title = getattr(stack.initiative, "title", None)
            return title if title else "Initiative"
        return "Business"

    stack_entries = _build_infrastructure_stack_entries(
        stacks,
        scope_label_fn=scope_label_fn,
    )

    stack_count = len(stack_entries)
    initiative_stack_count = len({entry["initiative_id"] for entry in stack_entries if entry["initiative_id"]})
    business_scoped_stack_count = stack_count - initiative_stack_count

    architecture_diagram = _build_infra_diagram_payload(
        stacks,
        stack_entries=stack_entries,
        diagram_namespace=f"business-infrastructure-stacks-{business.id}",
        scope_label_fn=scope_label_fn,
    )

    return {
        "stack_entries": stack_entries,
        "stack_count": stack_count,
        "initiative_stack_count": initiative_stack_count,
        "business_scoped_stack_count": business_scoped_stack_count,
        "architecture_diagram": architecture_diagram,
    }


def _tab_available_product_initiatives(business: Business) -> bool:
    from erieiron_autonomous_agent.business_level_agents.eng_lead import INITIATIVE_TITLE_BOOTSTRAP_ENVS
    return business.initiative_set.exclude(title="BOOTSTRAP_ENVS").exclude(title=INITIATIVE_TITLE_BOOTSTRAP_ENVS).exists()


def _tab_context_product_initiatives(business: Business) -> dict:
    from erieiron_autonomous_agent.business_level_agents.eng_lead import INITIATIVE_TITLE_BOOTSTRAP_ENVS
    existing_kpis = [
        kpi.name or kpi.kpi_id
        for kpi in business.businesskpi_set.order_by("name")
        if (kpi.name or kpi.kpi_id)
    ]
    return {
        "initiatives": business.initiative_set.exclude(title="BOOTSTRAP_ENVS").exclude(title=INITIATIVE_TITLE_BOOTSTRAP_ENVS).order_by("created_timestamp"),
        "business_kpis": existing_kpis
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


def _tab_available_analysis(business: Business) -> bool:
    return (business.businessanalysis_set.exists() or 
            business.businesslegalanalysis_set.exists() or 
            business.businesscapacityanalysis_set.exists() or 
            business.businessguidance_set.exists() or 
            business.businessceodirective_set.exists())


def _tab_context_analysis(business: Business) -> dict:
    business_analysis_list = list(
        business.businessanalysis_set.all().order_by("-created_timestamp")
    )
    business_legal_analysis_list = list(
        business.businesslegalanalysis_set.all().order_by("-created_timestamp")
    )
    business_capacity_analysis_list = list(
        business.businesscapacityanalysis_set.all().order_by("-created_timestamp")
    )
    business_guidance_list = list(
        business.businessguidance_set.all().order_by("-created_timestamp")
    )
    business_ceo_directives = list(
        business.businessceodirective_set.all().order_by("-created_timestamp")
    )

    analysis_entries = []

    for business_analysis in business_analysis_list:
        analysis_entries.append(
            {
                "type": "business_analysis",
                "record": business_analysis,
                "created_timestamp": business_analysis.created_timestamp,
            }
        )

    for business_legal_analysis in business_legal_analysis_list:
        analysis_entries.append(
            {
                "type": "business_legal_analysis",
                "record": business_legal_analysis,
                "created_timestamp": business_legal_analysis.created_timestamp,
            }
        )

    for business_capacity_analysis in business_capacity_analysis_list:
        analysis_entries.append(
            {
                "type": "business_capacity_analysis",
                "record": business_capacity_analysis,
                "created_timestamp": business_capacity_analysis.created_timestamp,
            }
        )

    for business_guidance in business_guidance_list:
        analysis_entries.append(
            {
                "type": "business_guidance",
                "record": business_guidance,
                "created_timestamp": business_guidance.created_timestamp,
            }
        )

    for ceo_directive in business_ceo_directives:
        analysis_entries.append(
            {
                "type": "business_ceo_directive",
                "record": ceo_directive,
                "created_timestamp": ceo_directive.created_timestamp,
            }
        )

    analysis_entries.sort(
        key=lambda entry: entry["created_timestamp"],
        reverse=True,
    )

    return {
        "business_analysis_list": business_analysis_list,
        "business_legal_analysis_list": business_legal_analysis_list,
        "business_capacity_analysis_list": business_capacity_analysis_list,
        "business_guidance_list": business_guidance_list,
        "business_ceo_directives": business_ceo_directives,
        "analysis_entries": analysis_entries,
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


def _tab_available_bug_report(business: Business) -> bool:
    return True


def _tab_context_bug_report(business: Business) -> dict:
    bug_fix_initiative = Initiative.objects.filter(
        business=business,
        initiative_type=InitiativeType.ENGINEERING,
        title__icontains="Bug Fix"
    ).first()
    
    return {
        "bug_fix_initiative": bug_fix_initiative
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
            if "availability_fn" in definition:
                available = definition["availability_fn"](business)
            else:
                available = True
            
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
    elif "context_fn" in tab_definition:
        context.update(tab_definition["context_fn"](business))
    
    breadcrumbs = [
        (reverse(view_portfolio), "Portfolio"),
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
    return True


def _initiative_tab_context_architecture(initiative: Initiative) -> dict:
    return {}


def _build_infra_diagram_payload(
        stacks: Iterable[InfrastructureStack],
        *,
        stack_entries: list[dict[str, Any]],
        diagram_namespace: str,
        scope_label_fn: Callable[[InfrastructureStack], str],
) -> dict[str, Any]:
    stack_list = list(stacks)
    dom_id = slugify(diagram_namespace) or "architecture-diagram"
    if not stack_list:
        return {
            "stacks": [],
            "nodes": [],
            "levels": [],
            "edges": [],
            "resource_count": 0,
            "stack_count": 0,
            "dom_id": dom_id,
        }

    address_lookup_by_stack: dict[int, dict[str, str]] = defaultdict(dict)
    global_address_lookup: dict[str, list[str]] = defaultdict(list)
    stack_resource_counts: dict[int, int] = defaultdict(int)
    nodes: list[dict[str, Any]] = []

    def _parse_resource(raw_resource: Any) -> dict[str, Any]:
        if isinstance(raw_resource, dict):
            return raw_resource

        if isinstance(raw_resource, str):
            trimmed = raw_resource.strip()
            if trimmed.startswith("{") or trimmed.startswith("["):
                try:
                    parsed = json.loads(trimmed)
                except json.JSONDecodeError as exc:
                    logging.exception(exc)
                    return {"raw": raw_resource}
                if isinstance(parsed, dict):
                    return parsed
                return {"raw": parsed}
            return {"raw": raw_resource}

        return {"raw": raw_resource}

    def _resolve_values(resource_payload: dict[str, Any]) -> dict[str, Any]:
        values = resource_payload.get("values")
        if isinstance(values, dict):
            return values
        return {}

    def _safe_string(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if isinstance(value, (int, float)):
            return str(value)
        return None

    def _stack_vars_dict(stack_obj: InfrastructureStack) -> dict[str, Any]:
        raw_stack_vars = getattr(stack_obj, "stack_vars", None)
        if isinstance(raw_stack_vars, dict):
            return raw_stack_vars
        if isinstance(raw_stack_vars, str):
            stripped = raw_stack_vars.strip()
            if stripped:
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return {}
                if isinstance(parsed, dict):
                    return parsed
        return {}

    def _extract_region(stack_obj: InfrastructureStack, values: dict[str, Any]) -> tuple[str, str]:
        candidates: list[str] = []

        for key in ("region", "aws_region", "provider_region"):
            candidate = _safe_string(values.get(key))
            if candidate:
                candidates.append(candidate)

        availability_zone = _safe_string(values.get("availability_zone"))
        if not availability_zone:
            availability_zones = values.get("availability_zones")
            if isinstance(availability_zones, (list, tuple)):
                availability_zone = _safe_string(common.first([az for az in availability_zones if _safe_string(az)]))
        if availability_zone:
            region_candidate = re.sub(r"[a-z]$", "", availability_zone)
            if region_candidate:
                candidates.append(region_candidate)

        stack_vars = _stack_vars_dict(stack_obj)
        for key in ("region", "aws_region", "AWS_REGION", "provider_region"):
            candidate = _safe_string(stack_vars.get(key))
            if candidate:
                candidates.append(candidate)

        metadata = {}
        try:
            metadata = stack_obj.get_iac_state_metadata()
        except Exception as metadata_exc:  # pragma: no cover - defensive logging
            logging.exception(metadata_exc)
        if isinstance(metadata, dict):
            candidate = _safe_string(metadata.get("region"))
            if candidate:
                candidates.append(candidate)

        region_value = None
        for candidate in candidates:
            if candidate:
                region_value = candidate
                break

        if not region_value:
            return "unknown-region", "Unknown Region"

        region_key = slugify(region_value) or region_value.lower()
        return region_key, region_value

    def _extract_subnet(values: dict[str, Any]) -> tuple[str, str]:
        subnet_value: str | None = None

        direct_keys = ["subnet_id", "subnet", "network_interface_subnet_id"]
        list_keys = ["subnet_ids", "subnets", "availability_zone_ids"]

        for key in direct_keys:
            candidate = _safe_string(values.get(key))
            if candidate:
                subnet_value = candidate
                break

        if not subnet_value:
            for key in list_keys:
                raw_candidate = values.get(key)
                if raw_candidate is None:
                    continue
                items = [
                    _safe_string(item)
                    for item in common.ensure_list(raw_candidate)
                ]
                item_values = [item for item in items if item]
                if item_values:
                    subnet_value = ", ".join(sorted(set(item_values)))
                    break

        if not subnet_value:
            return "no-subnet", "No Subnet"

        subnet_key = slugify(subnet_value) or re.sub(r"[^a-z0-9]+", "-", subnet_value.lower())
        if not subnet_key:
            subnet_key = "no-subnet"
        return subnet_key, subnet_value

    def _resource_type_label(resource_type: str | None) -> str:
        if not resource_type:
            return "Resource"
        cleaned = resource_type.replace(".", " ")
        if cleaned.startswith("aws_"):
            cleaned = "AWS " + cleaned[4:]
        cleaned = cleaned.replace("_", " ")
        return cleaned.title()

    def _resource_display_name(
        resource_payload: dict[str, Any],
        values: dict[str, Any],
        fallback: str,
        *,
        tag_name: str | None = None,
    ) -> str:
        candidates = [
            tag_name,
            values.get("name"),
            values.get("identifier"),
            values.get("id"),
            resource_payload.get("name"),
            fallback,
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return "Resource"

    nodes_by_uid: dict[str, dict[str, Any]] = {}

    for stack in stack_list:
        raw_resources = stack.resources
        if raw_resources in (None, {}, []):
            continue

        normalized_resources = common.ensure_list(raw_resources)
        stack_env_enum = EnvironmentType.valid_or(getattr(stack, "env_type", None), None)
        stack_env_label = stack_env_enum.label() if stack_env_enum else (stack.env_type or "Unknown")
        stack_type_enum = InfrastructureStackType.valid_or(getattr(stack, "stack_type", None), None)
        stack_type_label = stack_type_enum.label() if stack_type_enum else (stack.stack_type or "Unknown")

        for index, raw_resource in enumerate(normalized_resources):
            resource_payload = _parse_resource(raw_resource)
            values = _resolve_values(resource_payload)

            resource_type_value = resource_payload.get("type") or resource_payload.get("resource_type")
            resource_type_normalized = _safe_string(resource_type_value)
            if resource_type_normalized and resource_type_normalized.lower() in {"null", "null_resource"}:
                continue

            resource_type_base = (
                resource_type_normalized.split(".", 1)[0].lower()
                if resource_type_normalized
                else ""
            )
            if resource_type_base == "aws_region":
                continue

            resource_address = resource_payload.get("address") or resource_payload.get("name")
            identity_basis = resource_address or f"{stack.stack_name}:{resource_payload.get('type') or 'resource'}:{index}"
            uid = uuid.uuid5(uuid.NAMESPACE_URL, f"{diagram_namespace}:{stack.id}:{identity_basis}").hex

            if resource_address:
                address_lookup_by_stack[stack.id][resource_address] = uid
                global_address_lookup[resource_address].append(uid)

            depends_on_candidates = resource_payload.get("depends_on") or resource_payload.get("dependencies") or []
            depends_on_raw: list[str] = [
                str(dep)
                for dep in common.ensure_list(depends_on_candidates)
                if isinstance(dep, str)
            ]

            tags = values.get("tags") if isinstance(values.get("tags"), dict) else None
            tag_name = None
            if isinstance(tags, dict):
                raw_tag_name = tags.get("Name") or tags.get("name")
                if raw_tag_name:
                    tag_name = str(raw_tag_name)

            resource_name_value = values.get("name")
            identifier_value = values.get("identifier") or values.get("id")

            arn_value = values.get("arn")
            arn = str(arn_value) if isinstance(arn_value, str) else None
            console_url = None
            if arn:
                try:
                    console_url = aws_console_url_from_arn(arn)
                except Exception as exc:
                    logging.exception(exc)

            display_name = _resource_display_name(resource_payload, values, identity_basis, tag_name=tag_name)
            if display_name.strip().lower() == "null":
                continue

            resource_type_label = _resource_type_label(
                resource_payload.get("type") or resource_payload.get("resource_type")
            )
            if resource_type_label.strip().lower() == "aws region":
                continue

            region_key, region_label = _extract_region(stack, values)
            subnet_key, subnet_label = _extract_subnet(values)

            node = {
                "uid": uid,
                "address": resource_address,
                "type": resource_payload.get("type") or resource_payload.get("resource_type") or "unknown",
                "type_label": resource_type_label,
                "display_name": display_name,
                "resource_name": str(resource_name_value) if resource_name_value else "",
                "identifier": str(identifier_value) if identifier_value else "",
                "tag_name": tag_name or "",
                "provider": resource_payload.get("provider_name") or resource_payload.get("provider") or "",
                "module_address": resource_payload.get("module_address"),
                "mode": resource_payload.get("mode"),
                "arn": arn,
                "console_url": console_url,
                "depends_on_raw": depends_on_raw,
                "region_key": region_key,
                "region_label": region_label,
                "subnet_key": subnet_key,
                "subnet_label": subnet_label,
                "stack": {
                    "id": stack.id,
                    "name": stack.stack_name,
                    "namespace": stack.stack_namespace_token,
                    "type": stack.stack_type,
                    "type_label": stack_type_label,
                    "env": stack.env_type,
                    "env_label": stack_env_label,
                },
            }

            nodes.append(node)
            nodes_by_uid[uid] = node
            stack_resource_counts[stack.id] += 1

    for node in nodes:
        resolved_dependencies: list[str] = []
        unresolved_dependencies: list[str] = []
        stack_id = node["stack"]["id"]

        for dependency_key in node.get("depends_on_raw", []):
            resolved_uid = address_lookup_by_stack.get(stack_id, {}).get(dependency_key)
            if not resolved_uid:
                candidates = global_address_lookup.get(dependency_key, [])
                if len(candidates) == 1:
                    resolved_uid = candidates[0]
            if resolved_uid and resolved_uid in nodes_by_uid:
                resolved_dependencies.append(resolved_uid)
            else:
                unresolved_dependencies.append(dependency_key)

        node["dependencies"] = resolved_dependencies
        node["external_dependencies"] = unresolved_dependencies

    dependency_sources: set[str] = set()
    for node in nodes:
        dependency_sources.update(node.get("dependencies", []))

    filtered_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if not node.get("arn"):
            continue
        if node["uid"] in dependency_sources:
            continue
        filtered_nodes.append(node)

    nodes = filtered_nodes
    nodes_by_uid = {node["uid"]: node for node in nodes}

    stack_resource_counts = defaultdict(int)
    for node in nodes:
        stack_resource_counts[node["stack"]["id"]] += 1

    level_cache: dict[str, int] = {}

    def _resolve_level(node_uid: str, ancestry: set[str] | None = None) -> int:
        if node_uid in level_cache:
            return level_cache[node_uid]

        node_payload = nodes_by_uid.get(node_uid)
        if not node_payload:
            level_cache[node_uid] = 0
            return 0

        dependencies = node_payload.get("dependencies") or []
        if not dependencies:
            level_cache[node_uid] = 0
            return 0

        if ancestry is None:
            ancestry = set()

        if node_uid in ancestry:
            logger.warning(
                "Detected dependency cycle while computing architecture diagram levels",
                extra={"node": node_uid},
            )
            level_cache[node_uid] = 0
            return 0

        ancestry.add(node_uid)
        dependency_levels = [
            _resolve_level(dep_uid, ancestry)
            for dep_uid in dependencies
            if dep_uid in nodes_by_uid
        ]
        ancestry.remove(node_uid)

        level_value = (max(dependency_levels) + 1) if dependency_levels else 0
        level_cache[node_uid] = level_value
        return level_value

    levels: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        node_level = _resolve_level(node["uid"])
        node["level"] = node_level
        levels[node_level].append(node)

    level_entries: list[dict[str, Any]] = []
    for level_number in sorted(levels.keys()):
        resources_in_level = levels[level_number]
        resources_in_level.sort(
            key=lambda item: (
                item["stack"].get("env_label") or "",
                item["stack"].get("type_label") or "",
                item.get("type_label") or "",
                item.get("display_name") or "",
            )
        )
        for order_index, resource in enumerate(resources_in_level):
            resource["order_index"] = order_index
            resource["dom_id"] = f"architecture-node-{resource['uid']}"

        region_group_map: dict[str, dict[str, Any]] = OrderedDict()
        for resource in resources_in_level:
            region_key = resource.get("region_key") or "unknown-region"
            region_label = resource.get("region_label") or "Unknown Region"
            region_entry = region_group_map.setdefault(
                region_key,
                {
                    "key": region_key,
                    "label": region_label,
                    "subnet_map": OrderedDict(),
                },
            )

            subnet_key = resource.get("subnet_key") or "no-subnet"
            subnet_label = resource.get("subnet_label") or "No Subnet"
            subnet_entry = region_entry["subnet_map"].setdefault(
                subnet_key,
                {
                    "key": subnet_key,
                    "label": subnet_label,
                    "nodes": [],
                },
            )
            subnet_entry["nodes"].append(resource)

        region_groups: list[dict[str, Any]] = []
        ungrouped_nodes: list[dict[str, Any]] = []

        for region_entry in region_group_map.values():
            subnet_groups = list(region_entry["subnet_map"].values())
            subnet_groups.sort(key=lambda item: item["label"] or "")

            region_label_value = _safe_string(region_entry.get("label")) or ""
            region_key_value = region_entry.get("key") or ""
            is_placeholder_region = (
                not region_label_value
                or region_label_value.lower() == "unknown region"
                or region_key_value == "unknown-region"
            )

            all_region_nodes: list[dict[str, Any]] = []
            for subnet_entry in subnet_groups:
                all_region_nodes.extend(subnet_entry.get("nodes", []))

            if is_placeholder_region:
                ungrouped_nodes.extend(all_region_nodes)
                continue

            meaningful_subnet_groups: list[dict[str, Any]] = []
            subnet_free_nodes: list[dict[str, Any]] = []

            for subnet_entry in subnet_groups:
                subnet_label_value = _safe_string(subnet_entry.get("label")) or ""
                subnet_key_value = subnet_entry.get("key") or ""
                is_placeholder_subnet = (
                    not subnet_label_value
                    or subnet_label_value.lower() == "no subnet"
                    or subnet_key_value == "no-subnet"
                )

                if is_placeholder_subnet:
                    subnet_free_nodes.extend(subnet_entry.get("nodes", []))
                else:
                    meaningful_subnet_groups.append(subnet_entry)

            region_groups.append(
                {
                    "key": region_entry["key"],
                    "label": region_entry["label"],
                    "subnet_groups": meaningful_subnet_groups,
                    "nodes": subnet_free_nodes,
                }
            )

        region_groups.sort(key=lambda item: item["label"] or "")

        level_entries.append(
            {
                "level": level_number,
                "resources": resources_in_level,
                "region_groups": region_groups,
                "ungrouped_nodes": ungrouped_nodes,
            }
        )

    edges: list[dict[str, str]] = []
    for node in nodes:
        for dependency_uid in node.get("dependencies", []):
            if dependency_uid in nodes_by_uid:
                edges.append({"from": dependency_uid, "to": node["uid"]})

    total_resources = len(nodes)

    stack_meta_lookup = {str(entry.get("id")): entry for entry in stack_entries}
    stack_summaries: list[dict[str, Any]] = []
    for stack in stack_list:
        entry = stack_meta_lookup.get(str(stack.id), {})
        stack_type_enum = InfrastructureStackType.valid_or(getattr(stack, "stack_type", None), None)
        env_type_enum = EnvironmentType.valid_or(getattr(stack, "env_type", None), None)
        stack_summaries.append(
            {
                "id": stack.id,
                "name": stack.stack_name,
                "namespace": stack.stack_namespace_token,
                "type_label": entry.get("stack_type_label") or (stack_type_enum.label() if stack_type_enum else (stack.stack_type or "Unknown")),
                "env_label": entry.get("aws_env_label") or (env_type_enum.label() if env_type_enum else (stack.env_type or "Unknown")),
                "scope_label": entry.get("scope_label") or scope_label_fn(stack),
                "resource_count": stack_resource_counts.get(stack.id, 0),
            }
        )

    return {
        "stacks": stack_summaries,
        "nodes": nodes,
        "levels": level_entries,
        "edges": edges,
        "resource_count": total_resources,
        "stack_count": len(stack_list),
        "dom_id": dom_id,
    }


def _initiative_tab_available_architecture_diagram(_: Initiative) -> bool:
    return True


def _initiative_tab_context_infra_diagram(initiative: Initiative) -> dict:
    stacks = list(
        initiative.cloudformation_stacks
        .select_related("business", "initiative")
        .all()
        .order_by("env_type", "stack_type", "created_timestamp")
    )

    stack_entries = _build_infrastructure_stack_entries(
        stacks,
        scope_label_fn=lambda stack: "Initiative" if stack.initiative_id == initiative.id else "Business",
    )

    architecture_diagram = _build_infra_diagram_payload(
        stacks,
        stack_entries=stack_entries,
        diagram_namespace=f"initiative-architecture-{initiative.id}",
        scope_label_fn=lambda stack: "Initiative" if stack.initiative_id == initiative.id else "Business",
    )

    return {
        "architecture_diagram": architecture_diagram,
    }


def _initiative_tab_available_user_documentation(initiative: Initiative) -> bool:
    return True


def _initiative_tab_context_user_documentation(initiative: Initiative) -> dict:
    return {}


def _initiative_tab_available_tasks(initiative: Initiative) -> bool:
    return True


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


def _build_infrastructure_stack_entries(
        stacks: Iterable[InfrastructureStack],
        *,
        scope_label_fn: Callable[[InfrastructureStack], str] | None = None,
) -> list[dict]:
    default_region = EnvironmentType.DEV.get_aws_region()
    entries: list[dict] = []
    
    for stack in stacks:
        business = getattr(stack, "business", None)
        stack_type_enum = InfrastructureStackType.valid_or(getattr(stack, "stack_type", None), None)
        env_enum = EnvironmentType.valid_or(getattr(stack, "env_type", None), None)
        region = env_enum.get_aws_region() if env_enum else default_region
        
        metadata = stack.get_iac_state_metadata() if hasattr(stack, "get_iac_state_metadata") else {}
        provider = stack.iac_provider if hasattr(stack, "iac_provider") else getattr(settings, "SELF_DRIVING_IAC_PROVIDER", "opentofu").lower()
        provider = (provider or "unknown").lower()
        
        console_url = metadata.get("console_url")
        if provider == "cloudformation" and not console_url:
            if stack.stack_arn:
                console_url = (
                    f"https://console.aws.amazon.com/cloudformation/home#/stacks/stackinfo?stackId={quote(stack.stack_arn, safe='')}"
                )
            elif stack.stack_name:
                stack_name_encoded = quote(stack.stack_name, safe='')
                console_url = (
                    f"https://{region}.console.aws.amazon.com/cloudformation/home"
                    f"?region={region}#stacks?filteringStatus=active&filteringText={stack_name_encoded}"
                )
        
        logs_url = (
            f"https://{region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={region}#logsV2:log-groups$3FlogGroupNameFilter$3D{quote(stack.stack_namespace_token, safe='')}"
        )
        
        scope_label = scope_label_fn(stack) if scope_label_fn else (
            "Initiative" if stack.initiative_id else "Business"
        )
        
        state_label = metadata.get("state_label")
        if not state_label:
            if provider == "opentofu":
                state_label = metadata.get("workspace_name") or metadata.get("state_locator") or stack.stack_namespace_token
            else:
                state_label = metadata.get("state_locator") or stack.stack_name
        
        state_locator = stack.iac_state_locator if hasattr(stack, "iac_state_locator") else metadata.get("state_locator")
        if not state_locator:
            state_locator = stack.stack_namespace_token
        
        entries.append(
            {
                "id": str(stack.id),
                "business_id": stack.business_id,
                "business_name": getattr(business, "name", None) or getattr(business, "slug", None),
                "initiative_id": stack.initiative_id,
                "initiative_title": getattr(stack.initiative, "title", None) if stack.initiative_id else None,
                "stack_name": stack.stack_name,
                "stack_type_label": stack_type_enum.label() if stack_type_enum else (stack.stack_type or "Unknown"),
                "stack_type_value": stack.stack_type,
                "aws_env_label": env_enum.label() if env_enum else (stack.env_type or "Unknown"),
                "aws_env_value": stack.env_type,
                "iac_console_url": console_url,
                "cloudwatch_logs_url": logs_url,
                "stack_namespace_token": stack.stack_namespace_token,
                "stack_arn": stack.stack_arn,
                "created_timestamp": stack.created_timestamp,
                "updated_timestamp": stack.updated_timestamp,
                "scope_label": scope_label,
                "iac_provider": provider,
                "iac_state_label": state_label,
                "iac_state_locator": state_locator,
                "iac_state_metadata": metadata,
            }
        )
    
    entries.sort(
        key=lambda entry: (
            (entry.get("scope_label") or "").lower(),
            (entry.get("stack_name") or "").lower(),
        )
    )
    
    return entries


def _initiative_tab_available_infrastructure_stacks(_: Initiative) -> bool:
    return True


def _initiative_tab_context_infrastructure_stacks(initiative: Initiative) -> dict:
    stacks_qs = (
        initiative.cloudformation_stacks
        .select_related("initiative")
        .all()
        .order_by("env_type", "stack_type", "created_timestamp")
    )
    stacks = list(stacks_qs)

    def scope_label_fn(stack: InfrastructureStack) -> str:
        return "Initiative" if stack.initiative_id == initiative.id else "Business"

    stack_entries = _build_infrastructure_stack_entries(
        stacks,
        scope_label_fn=scope_label_fn,
    )

    architecture_diagram = _build_infra_diagram_payload(
        stacks,
        stack_entries=stack_entries,
        diagram_namespace=f"initiative-infrastructure-stacks-{initiative.id}",
        scope_label_fn=scope_label_fn,
    )

    return {
        "stack_entries": stack_entries,
        "child_task_count": initiative.tasks.count(),
        "architecture_diagram": architecture_diagram,
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


def _initiative_tab_available_bug_report(initiative: Initiative) -> bool:
    return True


def _initiative_tab_context_bug_report(initiative: Initiative) -> dict:
    return {}


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
        if "availability_fn" in definition:
            available = definition["availability_fn"](initiative)
        else:
            available = True
        
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
        (reverse(view_portfolio), "Portfolio"),
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
    return True


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


def _task_tab_available_debug_assistance(task, business, self_driving_task) -> bool:
    return True


def _task_tab_context_debug_assistance(task, business, self_driving_task) -> dict:
    return {
        "debug_steps": task.debug_steps
    }


def _task_tab_available_edit(task, business, self_driving_task) -> bool:
    return True


def _task_tab_context_edit(task, business, self_driving_task: SelfDrivingTask) -> dict:
    sandbox_path = self_driving_task.sandbox_path if self_driving_task else ""
    
    if self_driving_task:
        initiative = self_driving_task.task.initiative
    
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
    
    code_version_map = {
        code_version.code_file_id: code_version
        for code_version in iteration.codeversion_set.all()
    }
    
    code_files = []
    for code_file in code_file_datas:
        code_file_path = code_file.get('code_file_path')
        if code_file_path and code_file_path in code_file_map:
            codefile_id = code_file_map[code_file_path].id
            code_file['url'] = reverse(view_codefile, args=[codefile_id])
            code_file['code_version'] = code_version_map.get(codefile_id)
        
        code_files.append(code_file)
    
    return {
        "iteration": iteration,
        "code_files": code_files
    }


def _iteration_tab_available_evaluation(iteration: SelfDrivingTaskIteration, **_):
    return bool(getattr(iteration, "evaluation_json", None))


def _iteration_tab_context_evaluation(iteration: SelfDrivingTaskIteration, **_):
    return {}


def _iteration_tab_available_iac_logs(iteration: SelfDrivingTaskIteration, **_):
    logs = getattr(iteration, "iac_logs", None)
    if logs:
        return True
    return bool(getattr(iteration, "cloudformation_logs", None))


def _iteration_tab_available_codelog(iteration: SelfDrivingTaskIteration, **_):
    return getattr(iteration, "log_content_coding") or getattr(iteration, "log_content_execution")


def _iteration_tab_context_iac_logs(iteration: SelfDrivingTaskIteration, **_):
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
        (reverse(view_portfolio), "Portfolio"),
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
        (reverse(view_portfolio), "Portfolio"),
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
    self_driving_coder_agent_tofu.on_reset_task_test(task_id)
    
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
        return redirect(reverse('view_portfolio'))
    except Exception as e:
        messages.error(request, f'Error deleting task: {str(e)}')
        return redirect(reverse('view_portfolio'))


def action_add_business(request):
    if request.method != 'POST':
        raise Exception()
    
    business_name = rget(request, 'business_name', '').strip()
    business_description = rget(request, 'business_description', '').strip()
    operation_type = rget(request, 'operation_type', BusinessOperationType.ERIE_IRON_AUTONOMOUS.value)
    operation_type = (operation_type or '').strip() or BusinessOperationType.ERIE_IRON_AUTONOMOUS.value
    
    if not business_name:
        messages.error(request, 'Business name is required.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    
    if not business_description:
        messages.error(request, 'Business description is required.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    
    if not BusinessOperationType.valid(operation_type):
        messages.error(request, 'Invalid operation type selected.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    
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
    initiative.write_user_documentation()
    
    return redirect(reverse('view_initiative_tab', args=['architecture', initiative_id]))


def action_initiative_regenerate_user_documentation(request, initiative_id):
    initiative = get_object_or_404(Initiative, pk=initiative_id)
    
    initiative.write_user_documentation()
    
    return redirect(reverse('view_initiative_tab', args=['user-documentation', initiative_id]))


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
    
    return redirect(reverse('view_portfolio'))


def action_submit_bug_report(request, business_id):
    if request.method != 'POST':
        raise Exception()
    
    business = get_object_or_404(Business, id=business_id)
    
    bug_description = rget(request, 'bug_description', '').strip()
    
    if not bug_description:
        messages.error(request, 'Please provide a bug description.')
        return redirect(reverse('view_business_tab', args=['bug-report', business_id]))
    
    try:
        initiatives = [i.llm_data() for i in Initiative.objects.filter(business=business)]
        if not initiatives:
            messages.error(request, f'Bug report rejected: no initiatives yet for the business')
            return redirect(reverse('view_business_tab', args=['bug-report', business_id]))
        
        selection_data = system_agent_llm_interface.llm_chat(
            description=f"Select initiative for bug report in {business.name}",
            messages=[
                get_sys_prompt("eng_lead--initiative_selector.md"),
                LlmMessage.user_from_data("Available Initiatives", initiatives, "initiative"),
                LlmMessage.user_from_data("Bug Description", bug_description)
            ],
            tag_entity=business,
            model=LlmModel.OPENAI_GPT_5_NANO
        ).json()
        
        selected_initiative_id = selection_data.get('selected_initiative_id')
        rationale = selection_data.get('rationale', 'No rationale provided')
        
        if not selected_initiative_id:
            messages.error(request, f'Bug report rejected: {rationale}')
            return redirect(reverse('view_business_tab', args=['bug-report', business_id]))
        
        selected_initiative = get_object_or_404(Initiative, id=selected_initiative_id)
        parsed_data = system_agent_llm_interface.llm_chat(
            description=f"Parse bug report for {selected_initiative.title}",
            messages=[
                get_sys_prompt("eng_lead--bug_ingester.md"),
                LlmMessage.user_from_data("Bug Report", bug_description)
            ],
            tag_entity=selected_initiative,
            model=LlmModel.OPENAI_GPT_5_MINI
        ).json()
        
        task_id = f"task_bug_report_{business.service_token}_{common.gen_random_token(8)}"
        
        Task.objects.create(
            id=task_id,
            initiative=selected_initiative,
            task_type=TaskType.HUMAN_WORK,
            status=TaskStatus.NOT_STARTED,
            description=parsed_data.get('description'),
            risk_notes=parsed_data.get('risk_notes', ''),
            completion_criteria=parsed_data.get('completion_criteria', ['Bug is reproduced and fixed'])
        )
        
        messages.success(request, f'Bug report submitted successfully! A task has been created in the "{selected_initiative.title}" initiative. Selection rationale: {rationale}')
        return redirect(reverse('view_initiative_tab', args=["tasks", selected_initiative.id]))
    
    except Exception as e:
        messages.error(request, f'Error submitting bug report: {str(e)}')
        return redirect(reverse('view_business_tab', args=['bug-report', business_id]))


def action_submit_bug_report_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()
    
    initiative = get_object_or_404(Initiative, id=initiative_id)
    
    bug_description = rget(request, 'bug_description', '').strip()
    
    if not bug_description:
        messages.error(request, 'Please provide a bug description.')
        return redirect(reverse('view_initiative_tab', args=['bug-report', initiative_id]))
    
    try:
        # Use LLM to parse the bug report and extract structured information
        parsed_data = system_agent_llm_interface.llm_chat(
            description=f"Parse bug report for {initiative.title}",
            messages=[
                get_sys_prompt("eng_lead--bug_ingester.md"),
                LlmMessage.user_from_data("Bug Report", bug_description)
            ],
            tag_entity=initiative,
            model=LlmModel.OPENAI_GPT_5_MINI,
            verbosity=LlmVerbosity.MEDIUM
        ).json()
        
        Task.objects.create(
            id=f"task_bug_report_{initiative.business.service_token}_{common.gen_random_token(8)}",
            initiative=initiative,
            task_type=TaskType.HUMAN_WORK,
            status=TaskStatus.NOT_STARTED,
            description=parsed_data.get('description', f'Bug report: {bug_description[:100]}'),
            risk_notes=parsed_data.get('risk_notes', ''),
            completion_criteria=parsed_data.get('completion_criteria', ['Bug is reproduced and fixed'])
        )
        
        messages.success(request, 'Bug report submitted successfully! A task has been created in this initiative.')
        return redirect(reverse('view_initiative_tab', args=["tasks", initiative_id]))
    
    except Exception as e:
        messages.error(request, f'Error submitting bug report: {str(e)}')
        return redirect(reverse('view_initiative_tab', args=['bug-report', initiative_id]))


def action_submit_initiative_task(request, initiative_id):
    if request.method != 'POST':
        raise Exception()
    
    initiative = get_object_or_404(Initiative, id=initiative_id)
    
    raw_task_request = rget(request, 'task_request', '').strip()
    
    if not raw_task_request:
        messages.error(request, 'Please provide task details.')
        return redirect(reverse('view_initiative_tab', args=['tasks', initiative_id]))
    
    try:
        parsed_data = system_agent_llm_interface.llm_chat(
            description=f"Parse initiative task request for {initiative.title}",
            messages=[
                get_sys_prompt("eng_lead--task_ingester.md"),
                LlmMessage.user_from_data("Task Request", raw_task_request)
            ],
            output_schema="eng_lead--task_ingester.md.schema.json",
            tag_entity=initiative,
            model=LlmModel.OPENAI_GPT_5_MINI,
            verbosity=LlmVerbosity.MEDIUM
        ).json()
        
        description = (parsed_data.get('description') or raw_task_request).strip()
        completion_criteria = parsed_data.get('completion_criteria') or []
        completion_criteria = [
            str(item).strip()
            for item in common.ensure_list(completion_criteria)
            if str(item).strip()
        ]
        if not completion_criteria:
            completion_criteria = ['The task request has been fulfilled as described.']
        
        raw_risk_notes = parsed_data.get('risk_notes', '')
        if isinstance(raw_risk_notes, (list, tuple)):
            risk_notes = "\n".join(
                str(item).strip()
                for item in raw_risk_notes
                if str(item).strip()
            )
        else:
            risk_notes = str(raw_risk_notes or '').strip()
        
        raw_task_type = str(parsed_data.get('task_type', '') or '').strip()
        task_type = TaskType.valid_or(raw_task_type, TaskType.HUMAN_WORK)
        if raw_task_type and task_type == TaskType.HUMAN_WORK and raw_task_type != TaskType.HUMAN_WORK:
            logger.warning(
                "LLM returned invalid task_type '%s' for initiative %s; defaulting to HUMAN_WORK",
                raw_task_type,
                initiative.id,
            )
        
        Task.objects.create(
            id=f"{parsed_data.get('task_id')}_{common.gen_random_token(8)}",
            initiative=initiative,
            task_type=task_type,
            status=TaskStatus.NOT_STARTED,
            description=description,
            risk_notes=risk_notes,
            completion_criteria=completion_criteria
        )
        
        messages.success(request, 'Task submitted successfully! It has been added to this initiative.')
        return redirect(reverse('view_initiative_tab', args=['tasks', initiative_id]))
    
    except Exception as e:
        logger.exception(e)
        messages.error(request, f'Error submitting task: {str(e)}')
        return redirect(reverse('view_initiative_tab', args=['tasks', initiative_id]))


def action_add_initiative(request):
    if request.method != 'POST':
        raise Exception()
    
    title = rget(request, 'title', '').strip()
    initiative_type = rget(request, 'initiative_type', '').strip()
    description = rget(request, 'description', '').strip()
    requires_unit_tests = request.POST.get('requires_unit_tests') == 'on'
    
    if not title:
        messages.error(request, 'Initiative title is required.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    
    if not initiative_type:
        messages.error(request, 'Initiative type is required.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    
    if not description:
        messages.error(request, 'Initiative description is required.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    
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


@require_POST
def action_add_initiative_from_brief(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    
    brief = rget(request, 'initiative_brief', '').strip()
    if not brief:
        messages.error(request, 'Please provide a description for the initiative.')
        return redirect(reverse('view_business_tab', args=['product-initiatives', business_id]))
    
    business_kpis = list(business.businesskpi_set.all())
    
    existing_kpis = [k.name for k in business_kpis if k.name]
    parsed_payload = system_agent_llm_interface.llm_chat(
        description=f"Extract initiative details for {business.name}",
        messages=[
            get_sys_prompt("initiative--parse_brief.md"),
            LlmMessage.user_from_data(
                "Business Context",
                {
                    "business_name": business.name,
                    "existing_kpis": existing_kpis,
                }
            ),
            LlmMessage.user_from_data("Initiative Brief", brief)
        ],
        output_schema="initiative--parse_brief.md.schema.json",
        tag_entity=business,
        model=LlmModel.OPENAI_GPT_5,
        verbosity=LlmVerbosity.MEDIUM
    ).json()
    
    def _normalize_identifier(raw_value: str) -> str:
        cleaned = (raw_value or "").lower()
        if not cleaned:
            return ""
        
        normalized_chars = [
            char if char.isalnum() or char == '_'
            else '_'
            for char in cleaned
        ]
        candidate = ''.join(normalized_chars)
        normalized = '_'.join(segment for segment in candidate.split('_') if segment)
        return normalized[:200]
    
    title = (parsed_payload.get('title') or 'New Initiative').strip()
    description = (parsed_payload.get('description') or brief).strip()
    desired_identifier = _normalize_identifier(parsed_payload.get('initiative_id', ''))
    fallback_identifier = _normalize_identifier(title) or _normalize_identifier(business.name)
    if not fallback_identifier:
        fallback_identifier = _normalize_identifier(f"initiative_{common.gen_random_token(6)}")
    
    initiative_identifier = desired_identifier or fallback_identifier
    initiative_identifier = initiative_identifier.rstrip('_')[:200]
    if not initiative_identifier:
        initiative_identifier = fallback_identifier
    
    base_identifier = initiative_identifier
    suffix = 1
    while Initiative.objects.filter(id=initiative_identifier).exists():
        suffix_str = f"_{suffix}"
        trimmed_base = base_identifier[: max(1, 200 - len(suffix_str))].rstrip('_')
        if not trimmed_base:
            trimmed_base = fallback_identifier[: max(1, 200 - len(suffix_str))]
            trimmed_base = trimmed_base.rstrip('_') or f"initiative"
        initiative_identifier = f"{trimmed_base}_{suffix}"
        suffix += 1
    
    kpi_entries = [str(k).strip() for k in common.ensure_list(parsed_payload.get('kpis')) if str(k).strip()]
    
    expected_kpi_lift = {kpi: 0.0 for kpi in kpi_entries}
    
    initiative = Initiative.objects.create(
        id=initiative_identifier,
        business=business,
        title=title,
        description=description,
        initiative_type=InitiativeType.PRODUCT,
        priority=Level.MEDIUM,
        requires_unit_tests=True,
        expected_kpi_lift=expected_kpi_lift
    )
    
    if kpi_entries and business_kpis:
        kpi_lookup: dict[str, BusinessKPI] = {}
        for kpi in business_kpis:
            name_key = (kpi.name or '').strip().lower()
            if name_key:
                kpi_lookup.setdefault(name_key, kpi)
            id_key = (getattr(kpi, 'kpi_id', '') or '').strip().lower()
            if id_key:
                kpi_lookup.setdefault(id_key, kpi)
        
        matched_kpis = []
        for kpi_name in kpi_entries:
            key = kpi_name.lower()
            kpi_obj = kpi_lookup.get(key)
            if kpi_obj:
                matched_kpis.append(kpi_obj)
        
        if matched_kpis:
            initiative.linked_kpis.set(matched_kpis)
    
    PubSubManager.publish_id(
        PubSubMessageType.INITIATIVE_DEFINITION_REQUESTED,
        initiative.id
    )
    
    messages.success(request, f'Initiative "{initiative.title}" created successfully!')
    return redirect(reverse('view_initiative', args=[initiative.id]))


@require_POST
def action_business_production_push(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    
    initiative, _ = Initiative.objects.get_or_create(
        business=business,
        title=InitiativeNames.OPERATIONAL_TASKS,
        defaults={
            "id": str(uuid.uuid4()),
            "description": "Operational continuity tasks and production deployments.",
            "priority": Level.MEDIUM,
            "initiative_type": InitiativeType.ENGINEERING,
            "requires_unit_tests": False,
        }
    )
    
    task = Task.objects.create(
        id=f"task_production_push_{business.service_token}_{common.gen_random_token(8)}",
        initiative=initiative,
        task_type=TaskType.PRODUCTION_DEPLOYMENT,
        status=TaskStatus.NOT_STARTED,
        description=(
            "Production push requested on "
            f"{formats.date_format(timezone.now(), 'DATETIME_FORMAT')}"
        ),
        risk_notes="",
        completion_criteria=["Deployment completed successfully in production."],
        comment_requests=[],
        attachments=[],
        input_fields={},
        output_fields=[],
        requires_test=False,
        created_by=getattr(request.user, "username", None) or "system",
    )
    
    messages.success(
        request,
        f"Production push task queued in '{initiative.title}'."
    )
    
    return redirect(reverse('view_task', args=[task.id]))


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
        requires_unit_tests = request.POST.get('requires_unit_tests') == 'on'
        green_lit = request.POST.get('green_lit') == 'on'
        
        # Check if green_lit status is changing from False to True
        was_green_lit = initiative.green_lit
        
        # Prepare update data
        update_data = {
            'title': title,
            'description': description,
            'architecture': architecture or None,
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
        return redirect(reverse('view_portfolio_tab', args=['initiatives']))
    except Exception as e:
        logging.exception(e)
        messages.error(request, f'Error updating initiative: {str(e)}')
        return redirect(reverse('view_initiative', args=[initiative_id]))


def action_delete_initiative(request, initiative_id):
    if request.method != 'POST':
        raise Exception()
    
    initiative = get_object_or_404(Initiative, id=initiative_id)
    try:
        initiative_title = initiative.title
        initiative.delete()
        messages.success(request, f'Initiative "{initiative_title}" deleted successfully!')
    except Exception as e:
        logging.exception(e)
        messages.error(request, f'Error deleting initiative: {str(e)}')
    
    return redirect(reverse('view_business_tab', args=['initiatives', initiative.business_id]))


def action_find_business(request):
    business_name = rget(request, 'business_name', '').strip()
    business_description = rget(request, 'business_description', '').strip()
    
    business = Business.objects.create(
        name=f"{Constants.NEW_BUSINESS_NAME_PREFIX} {common.get_now()}",
        status=BusinessStatus.IDEA,
        source=BusinessIdeaSource.BUSINESS_FINDER_AGENT
    )
    
    PubSubManager.publish(
        PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
        payload={
            "placehold_business_id": business.id
        }
    )
    
    return redirect(reverse('view_portfolio_tab', args=['portfolio']))


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
        return redirect(reverse('view_portfolio'))
    except Exception as e:
        messages.error(request, f'Error updating guidance: {str(e)}')
        return redirect(reverse('view_task', args=[task_id]))


@require_POST
@json_endpoint
def action_debug_assistance(request, task_id):
    try:
        task = get_object_or_404(Task, pk=task_id)
        question = rget(request, 'question', '').strip()
        
        initiative = task.initiative
        business = initiative.business
        
        debug_steps_html = system_agent_llm_interface.llm_chat(
            description=f"Debug assistance for task: {task.id}",
            messages=[
                get_sys_prompt("debug_assistance.md"),
                LlmMessage.user_from_data("Task Description", task.description),
                LlmMessage.user_from_data("Task Completion Criteria", task.completion_criteria or "None specified"),
                LlmMessage.user_from_data("Task Risk Notes", task.risk_notes or "None specified"),
                LlmMessage.user_from_data("Debug Question", question) if question else None
            ],
            tag_entity=initiative,
            model=LlmModel.OPENAI_GPT_5_MINI
        ).text.replace('\n', '<br>')
        
        task.debug_steps = debug_steps_html
        task.save()
        
        return {"success": True, "debug_steps": debug_steps_html}
    
    except Task.DoesNotExist:
        return {"success": False, "error": "Task not found"}
    except Exception as e:
        logging.exception(e)
        return {"success": False, "error": f"Error getting debug assistance: {str(e)}"}


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
        completion_criteria = rget(request, 'completion_criteria', "").strip()
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
    except Task.DoesNotExist as e:
        logging.exception(e)
        messages.error(request, 'Task not found.')
        return redirect(reverse('view_portfolio'))
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
        erie_iron_business = Business.get_erie_iron_business()
        if business.id == erie_iron_business.id:
            name = erie_iron_business.name
            
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
        domain = rget(request, 'domain', '').strip()
        domain_certificate_arn = rget(request, 'domain_certificate_arn', '').strip()

        # Prepare update data
        update_data = {
            'name': name,
            'domain': domain or None,
            'domain_certificate_arn': domain_certificate_arn or None,
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
        return redirect(reverse('view_portfolio'))
    except Exception as e:
        logging.exception(e)
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
        return redirect(reverse('view_portfolio'))
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
            redirect_url = reverse('view_portfolio_tab', args=['tools'])
        
        if running_process.kill_process():
            messages.success(request, f'Process {process_id} killed successfully!')
        else:
            messages.warning(request, f'Failed to kill process {process_id} - it may have already terminated.')
        
        return redirect(redirect_url)
    except RunningProcess.DoesNotExist:
        messages.error(request, 'Process not found.')
        return redirect(reverse('view_portfolio_tab', args=['tools']))
    except Exception as e:
        messages.error(request, f'Error killing process: {str(e)}')
        return redirect(reverse('view_portfolio_tab', args=['tools']))


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
        return redirect(reverse('view_portfolio'))
    except Exception as e:
        messages.error(request, f'Error deleting iteration: {str(e)}')
        return redirect(reverse('view_portfolio'))


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
        return redirect(reverse('view_portfolio'))
    except Exception as e:
        messages.error(request, f'Error rolling back iteration: {str(e)}')
        return redirect(reverse('view_portfolio'))


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
        return redirect(reverse('view_portfolio_tab', args=['lessons']))
    except AgentLesson.DoesNotExist:
        messages.error(request, 'Lesson not found.')
        return redirect(reverse('view_portfolio_tab', args=['lessons']))
    except Exception as e:
        messages.error(request, f'Error updating lesson: {str(e)}')
        return redirect(reverse('view_portfolio_tab', args=['lessons']))


@require_POST
def action_destroy_stack(request, stack_id):
    stack = get_object_or_404(InfrastructureStack, pk=stack_id)
    
    if EnvironmentType.PRODUCTION.eq(stack.env_type):
        messages.error(request, 'Production stacks cannot be destroyed from this interface.')
        return redirect(reverse('view_stack', args=[stack_id]))

    stack_display = stack.stack_name or stack.stack_namespace_token or str(stack.id)
    business_id = stack.business_id

    try:
        stack.delete_resources()
    except Exception as exc:
        logger.exception(exc)
        messages.error(
            request,
            f'Failed to destroy infrastructure resources for stack "{stack_display}": {exc}'
        )
        return redirect(reverse('view_stack', args=[stack_id]))

    try:
        stack.delete()
    except Exception as exc:
        logger.exception(exc)
        messages.error(request, f'Failed to delete stack "{stack_display}": {exc}')
        return redirect(reverse('view_stack', args=[stack_id]))

    messages.success(request, f'Stack "{stack_display}" destroyed successfully.')
    return redirect(reverse('view_business_tab', args=['infrastructure-stacks', business_id]))


def view_stack(request, stack_id):
    stack = get_object_or_404(InfrastructureStack, pk=stack_id)
    business = stack.business
    
    tabs = _build_business_tabs(business)
    stack_entry = common.first(_build_infrastructure_stack_entries([stack])) or {}
    
    stack_type_enum = InfrastructureStackType.valid_or(getattr(stack, "stack_type", None), None)
    env_type_enum = EnvironmentType.valid_or(getattr(stack, "env_type", None), None)
    
    stack_type_label = stack_type_enum.label() if stack_type_enum else (stack.stack_type or "Unknown")
    environment_label = env_type_enum.label() if env_type_enum else (stack.env_type or "Unknown")
    scope_label = (
        getattr(stack.initiative, "title", "Initiative")
        if stack.initiative_id
        else "Business"
    )
   
    stack_vars_pretty = common.json_format_pretty(stack.stack_vars)
    iac_state_metadata = stack_entry.get("iac_state_metadata")
    iac_state_metadata_pretty = common.json_format_pretty(iac_state_metadata)
    
    raw_resources = stack.resources
    if raw_resources in (None, {}, []):
        normalized_resources: list[Any] = []
    else:
        normalized_resources = common.ensure_list(raw_resources)
    
    stack_resources: list[dict[str, Any]] = []
    for raw_resource in normalized_resources:
        parsed_resource: Any = raw_resource
        
        if isinstance(raw_resource, str):
            trimmed = raw_resource.strip()
            if trimmed.startswith("{") or trimmed.startswith("["):
                try:
                    parsed_resource = json.loads(trimmed)
                except json.JSONDecodeError as exc:
                    logging.exception(exc)
                    parsed_resource = {"raw": raw_resource}
            else:
                parsed_resource = {"raw": raw_resource}
        
        if not isinstance(parsed_resource, dict):
            stack_resources.append(
                {
                    "address": None,
                    "name": None,
                    "type": None,
                    "mode": None,
                    "provider": None,
                    "module_address": None,
                    "arn": None,
                    "console_url": None,
                    "values_pretty": common.json_format_pretty(parsed_resource),
                    "resource_pretty": common.json_format_pretty(parsed_resource),
                }
            )
            continue
        
        values = parsed_resource.get("values")
        if not isinstance(values, dict):
            values = {}
        
        arn = values.get("arn")
        console_url = None
        if arn:
            try:
                console_url = aws_console_url_from_arn(str(arn))
            except Exception as exc:
                logging.exception(exc)
        
        stack_resources.append(
            {
                "address": parsed_resource.get("address") or parsed_resource.get("name"),
                "name": parsed_resource.get("name"),
                "type": parsed_resource.get("type"),
                "mode": parsed_resource.get("mode"),
                "provider": parsed_resource.get("provider_name") or parsed_resource.get("provider"),
                "module_address": parsed_resource.get("module_address"),
                "arn": arn,
                "console_url": console_url,
                "values_pretty": common.json_format_pretty(values),
                "resource_pretty": common.json_format_pretty(parsed_resource),
            }
        )
    
    grouped_resources_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in stack_resources:
        resource_type = (resource.get("type") or "Unknown").strip() or "Unknown"
        grouped_resources_map[resource_type].append(resource)
    
    resource_groups: list[dict[str, Any]] = []
    for resource_type in sorted(grouped_resources_map.keys(), key=lambda value: value.lower()):
        resources_in_group = grouped_resources_map[resource_type]
        resources_in_group.sort(
            key=lambda item: (item.get("address") or item.get("name") or "").lower()
        )
        anchor = slugify(resource_type) or "resource-type"
        resource_groups.append(
            {
                "type": resource_type,
                "anchor": f"resource-type-{anchor}",
                "resources": resources_in_group,
            }
        )
    
    business_url = reverse('view_business', args=[business.id])
    initiative_url = (
        reverse('view_initiative', args=[stack.initiative_id])
        if stack.initiative_id
        else None
    )
    
    context = {
        "business": business,
        "tabs": tabs,
        "active_tab": "infrastructure-stacks",
        "tab_template": "business/stack_detail.html",
        "stack": stack,
        "stack_entry": stack_entry,
        "stack_type_label": stack_type_label,
        "environment_label": environment_label,
        "scope_label": scope_label,
        "stack_vars_pretty": stack_vars_pretty,
        "iac_state_metadata_pretty": iac_state_metadata_pretty,
        "resource_groups": resource_groups,
        "business_url": business_url,
        "initiative_url": initiative_url,
    }
    
    breadcrumbs = [
        (reverse(view_portfolio), "Portfolio"),
        (business_url, business.name),
    ]
    
    if tabs:
        breadcrumbs.append(
            (
                reverse('view_business_tab', args=['infrastructure-stacks', business.id]),
                next(
                    (
                        tab.get("label")
                        for tab in tabs
                        if tab.get("slug") == 'infrastructure-stacks'
                    ),
                    'Infrastructure Stacks'
                ),
            )
        )
    
    stack_display_name = stack.stack_name or stack.stack_namespace_token or str(stack.id)
    breadcrumbs.append((reverse('view_stack', args=[stack.id]), stack_display_name))
    
    return send_response(
        request,
        "business/business_base.html",
        context,
        breadcrumbs=breadcrumbs,
    )


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
        (reverse(view_portfolio), Business.get_erie_iron_business().name),
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
        (reverse(view_portfolio), "Portfolio")
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


def view_pubsub_message_details(request, message_id):
    message = get_object_or_404(PubSubMessage, id=message_id)
    
    return send_response(
        request,
        "portfolio/portfolio_base.html",
        {
            "business": Business.get_erie_iron_business(),
            "tabs": _build_portfolio_tabs(Business.get_erie_iron_business()),
            "message": message,
            "payload_json": json.dumps(message.payload, indent=2) if message.payload else None,
            "tab_template": "pubsub/message_details.html",
            "redirect_target": reverse('view_portfolio_tab', args=['pubsub-messages']),
        },
        breadcrumbs=[
            (reverse('view_portfolio'), Business.get_erie_iron_business().name),
            (reverse('view_portfolio_tab', args=['pubsub-messages']), 'PubSub Messages'),
            (None, f'Message {str(message_id)[:8]}'),
        ]
    )
    
    return send_response(request, "pubsub/message_details.html", context, breadcrumbs=breadcrumbs)


@require_POST
def fetch_pubsub_messages(request):
    page_size = int(request.POST.get('page_size', 20))
    page_number = int(request.POST.get('page_number', 0))
    sort_by = request.POST.get('sort_by', 'created_at')

    message_types_raw = request.POST.get('message_types', '')
    statuses_raw = request.POST.get('statuses', '')

    message_types = [value for value in message_types_raw.split(',') if value]
    statuses = [value for value in statuses_raw.split(',') if value]

    offset = page_number * page_size
    messages_qs = PubSubMessage.objects.all()
    if message_types:
        messages_qs = messages_qs.filter(message_type__in=message_types)
    if statuses:
        messages_qs = messages_qs.filter(status__in=statuses)

    messages = messages_qs.order_by(f"-{sort_by}")[offset:offset + page_size]

    context = {
        "pubsub_messages": messages,
        "redirect_target": reverse('view_portfolio_tab', args=['pubsub-messages']),
    }
    
    return render(request, "pubsub/message_list_partial.html", context)


@require_POST
def action_delete_pubsub_message(request, message_id):
    message = get_object_or_404(PubSubMessage, id=message_id)
    message.delete()
    
    messages.success(request, f"PubSub message {str(message_id)[:8]} deleted successfully.")
    return redirect(reverse('view_portfolio_tab', args=['pubsub-messages']))


@require_POST
def action_retry_pubsub_message(request, message_id):
    message = get_object_or_404(PubSubMessage, id=message_id)
    
    PubSubMessage.reprocess([message.id], message.env)
    messages.success(request, f"PubSub message {str(message_id)[:8]} has been marked for retry.")

    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER')
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        return HttpResponseRedirect(next_url)

    return HttpResponseRedirect(reverse('view_pubsub_message_details', args=[message_id]))
