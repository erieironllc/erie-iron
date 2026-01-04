import glob
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List

import markdown
from django import template
from django.db import models
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name

import settings
from erieiron_autonomous_agent.models import (
    LlmRequest,
    Business,
    Initiative,
    Task,
    SelfDrivingTask,
    SelfDrivingTaskIteration,
)
from erieiron_common import common, date_utils, ErieIronJSONEncoder
from erieiron_common.aws_utils import get_cloudwatch_url
from erieiron_common.enums import LlmModel
from erieiron_common.llm_apis.llm_constants import get_token_count

register = template.Library()


@register.filter(name='dedupe_divers')
def dedupe_divers(tabs):
    deduped_tabs = []
    prev_tab_is_divider = False
    for tab in common.ensure_list(tabs):
        if tab.get("is_divider"):
            if prev_tab_is_divider:
                prev_tab_is_divider = False
            else:
                prev_tab_is_divider = True
                deduped_tabs.append(tab)
        else:
            prev_tab_is_divider = False
            deduped_tabs.append(tab)
    
    return deduped_tabs


@register.simple_tag(takes_context=True)
def top_nav_dropdowns(context):
    breadcrumbs = context.get("breadcrumbs") or []
    if not breadcrumbs:
        return []
    
    try:
        from erieiron_ui import views as ui_views
    except Exception:
        return []
    
    tab_cache = {}
    
    def cached(key, builder):
        if key not in tab_cache:
            try:
                tab_cache[key] = builder() or []
            except Exception:
                tab_cache[key] = []
        return tab_cache[key]
    
    try:
        erie_business = Business.get_erie_iron_business()
    except Exception:
        erie_business = None
    
    business_obj: Business | None = context.get("business")
    initiative_obj: Initiative | None = context.get("initiative")
    task_obj: Task | None = context.get("task")
    iteration_obj: SelfDrivingTaskIteration | None = context.get("iteration")
    
    self_driving_task: SelfDrivingTask | None = context.get("self_driving_task")
    if not self_driving_task and task_obj is not None:
        self_driving_task = getattr(task_obj, "selfdrivingtask", None)
    if not self_driving_task and iteration_obj is not None:
        self_driving_task = getattr(iteration_obj, "self_driving_task", None)
    
    active_tabs = context.get("tabs") or []
    active_slug = context.get("active_tab")
    
    iteration_label = None
    if iteration_obj and getattr(iteration_obj, "version_number", None) is not None:
        iteration_label = f"Iteration {iteration_obj.version_number}"
    
    entries = []
    for idx, crumb in enumerate(breadcrumbs):
        label = common.default_str(common.get(crumb, "label"))
        url = common.default_str(common.get(crumb, "url"))
        
        tabs = []
        active = None
        
        if erie_business and idx == 0:
            tabs = cached(("portfolio", erie_business.id), lambda: ui_views._build_portfolio_tabs(erie_business))
        elif business_obj and label == business_obj.name:
            tabs = cached(("business", business_obj.id), lambda: ui_views._build_business_tabs(business_obj))
        elif initiative_obj and label == getattr(initiative_obj, "title", ""):
            tabs = cached(("initiative", initiative_obj.id), lambda: ui_views._build_initiative_tabs(initiative_obj))
        elif task_obj and label == task_obj.get_name():
            related_business = business_obj or getattr(task_obj.initiative, "business", None)
            tabs = cached(("task", task_obj.id), lambda: ui_views._build_task_tabs(task_obj, related_business, self_driving_task))
        elif iteration_label and label == iteration_label:
            tabs = active_tabs
            active = active_slug
        else:
            if active_tabs:
                matching_tab = next((t for t in active_tabs if common.default_str(t.get("label")) == label), None)
                if matching_tab:
                    tabs = active_tabs
                    active = active_slug
        
        entries.append({
            "label": label,
            "url": url,
            "tabs": tabs,
            "active": active,
        })
    
    return entries


@register.filter(name='dynamic_format')
def dynamic_format(content):
    try:
        content = json.loads(content)
    except:
        ...
    
    if isinstance(content, dict):
        return mark_safe(f"<div class='pre'>{pprint_json(content)}</div>")
    else:
        return mark_safe(markdown.markdown(str(content)))


@register.filter(name='json_to_md')
def json_to_md(json_content, filter_def=None, use_default_wrapper=True):
    return json_to_div(json_content, filter_def, use_default_wrapper, apply_md=True)


@register.filter(name='json_to_pre')
def json_to_pre(json_content, filter_def=None, use_default_wrapper=True, make_pre=False, apply_md=False):
    return json_to_div(json_content, filter_def, use_default_wrapper, make_pre=True)


@register.filter(name='json_to_div')
def json_to_div(json_content, filter_def=None, use_default_wrapper=True, make_pre=False, apply_md=False, _depth=1):
    if json_content is None:
        return ""

    # Parse filter definition
    only_fields = []
    exclude_fields = []
    keys = []
    filter_def_items = common.safe_split(filter_def, ",")
    for filter_def_item in filter_def_items:
        if filter_def_item.startswith("-"):
            exclude_fields.append(filter_def_item[1:])
        elif "*" not in filter_def_item:
            only_fields.append(filter_def_item)
            keys.append(filter_def_item)

    if filter_def and "*" in filter_def:
        keys = [
            *keys,
            *[k for k in json_content.keys() if k not in only_fields] 
        ]

    # Try to JSON-parse when appropriate
    if not isinstance(json_content, (dict, list)):
        try:
            json_content = json.loads(json_content)
        except Exception:
            # Fallback non-JSON content
            if apply_md:
                json_content = markdown.markdown(str(json_content))
            wrapper = "div" if use_default_wrapper else "li"
            return mark_safe(f"<{wrapper} class='json_to_div--container {'pre' if make_pre else ''}'>{json_content}</{wrapper}>")

    # Handle lists
    if isinstance(json_content, list):
        rendered = []
        for item in json_content:
            rendered.append(
                json_to_div(
                    item,
                    filter_def=filter_def,
                    use_default_wrapper=False,
                    make_pre=make_pre,
                    apply_md=apply_md,
                    _depth=_depth
                )
            )
        return mark_safe("\n".join(rendered))

    # Handle dicts
    parts = []
    keys_to_render = keys or json_content.keys()
    heading_level = min(_depth + 3, 6)  # h2 at root, smaller as depth increases

    for k in keys_to_render:
        if k in exclude_fields:
            continue

        display_label = k.replace("_", " ").title()
        v = json_content.get(k)

        # Render value recursively
        if isinstance(v, (dict, list)):
            sub_html = json_to_div(
                v,
                filter_def=None,
                use_default_wrapper=True,
                make_pre=make_pre,
                apply_md=apply_md,
                _depth=_depth + 1
            )
            parts.append(f"""
                <div class="json_to_div--section depth-{_depth}" style="margin-left: {(_depth-1)*20}px">
                    <h{heading_level}>{display_label}</h{heading_level}>
                    {sub_html}
                </div>
            """)
        else:
            if isinstance(v, dict):
                # Shouldn't hit here due to the recursive branch above, but safe fallback
                v = json.dumps(v, indent=4, cls=ErieIronJSONEncoder)

            if apply_md:
                v = markdown.markdown(str(v))
            else:
                v = str(v)

            parts.append(f"""
                <div class="json_to_div--container depth-{_depth}" style="margin-left: {(_depth-1)*20}px">
                    <h{heading_level}>{display_label}</h{heading_level}>
                    <div class="{'pre' if make_pre else ''}">{v}</div>
                </div>
            """)

    return mark_safe("".join(parts))


@register.filter(name='token_count')
def token_count(v):
    return get_token_count(LlmModel.OPENAI_GPT_5_1, json.dumps(v))


@register.filter(name='json_dumps')
def json_dumps(value, indent=2):
    if value is None:
        return ""
    
    try:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return mark_safe(value)
        
        dump_kwargs = {"cls": ErieIronJSONEncoder}
        
        try:
            if indent is not None:
                indent_int = int(indent)
                if indent_int >= 0:
                    dump_kwargs["indent"] = indent_int
        except (TypeError, ValueError):
            pass
        
        return mark_safe(json.dumps(value, **dump_kwargs))
    except Exception:
        return mark_safe(str(value))


@register.filter(name='highlight')
def highlight_code(code, lang='python'):
    try:
        lexer = get_lexer_by_name(lang, stripall=True)
        formatter = HtmlFormatter(linenos=False, cssclass="highlight")
        return mark_safe(highlight(code, lexer, formatter))
    except Exception as e:
        return code


@register.filter(name='markdown')
def markdown_format(text):
    if not text:
        return text
    
    if isinstance(text, list):
        text = "\n".join(text)
    elif isinstance(text, list):
        text = json.dumps(text, indent=4)
    
    return mark_safe(markdown.markdown(text, tab_length=4))


@register.filter(name='llm_msg_cost')
def llm_msg_cost(content: str, llm_model: str):
    llm_model = LlmModel(llm_model)
    
    return llm_model.get_input_price(content)


@register.filter(name='llm_cost')
def llm_cost(llm_requests: list[LlmRequest]):
    if not len(llm_requests):
        return 0
    
    return sum([l.price for l in llm_requests])


@register.filter
def dictsort_case_insensitive(value, arg):
    try:
        return sorted(value, key=lambda x: x.get(arg, '').lower())
    except (AttributeError, TypeError):
        return value


@register.simple_tag
def timestamp_static(orig_filename):
    static_dir_root = Path(settings.STATIC_COMPILED_DIR)
    if not static_dir_root.is_absolute():
        static_dir_root = Path.cwd() / static_dir_root

    filename, ext = common.get_filename_and_extension(f"{static_dir_root}/{orig_filename}")
    files_with_time: list[tuple[Path, int]] = []

    for file_path in glob.glob(os.path.join(static_dir_root, f"{filename}-*.{ext}")):
        path = Path(file_path)
        base_name = path.name
        timestamp = base_name.split('-')[1].split('.')[0]
        files_with_time.append((path, int(timestamp)))

    files_with_time.sort(key=lambda x: x[1], reverse=True)

    static_root = Path(settings.STATIC_ROOT)
    target_dir = static_root / static_dir_root.name
    target_dir.mkdir(parents=True, exist_ok=True)

    latest_matching_file = orig_filename  # default to the orig name in the case it's not timestamped
    for idx, (file_path, _) in enumerate(files_with_time):
        destination_path = target_dir / file_path.name
        if idx == 0:
            latest_matching_file = file_path.name
            shutil.copy2(file_path, destination_path)
        else:
            common.quietly_delete(file_path)
            common.quietly_delete(destination_path)

    return f"/static/{static_dir_root.name}/{latest_matching_file}"


@register.filter
def to_json(s):
    try:
        return json.loads(s)
    except:
        try:
            return json.loads(s.replace("'", "\""))
        except:
            return {}


@register.filter
def cloudwatch_url(d):
    d = date_utils.ensure_datetime(d)
    
    return mark_safe(f'<a style="white-space: nowrap" href="{get_cloudwatch_url(d)}">{date_utils.format_with_time(d)}</a>')


@register.filter
def rsplit(s):
    try:
        return common.default_str(s).rsplit(".", 1)[1]
    except:
        return ""


@register.filter
def append_unique(s):
    return replace_dashes(f"{s}{uuid.uuid4()}")


@register.filter
def format_millis_to_seconds(millis, decimal_places=0):
    try:
        return common.format_millis_to_hr_min_sec(millis, decimal_places=decimal_places)
    except:
        return ""


@register.filter
def swizzle_current(s: str):
    replacements = [
        ("current", "previous")
    ]
    output = s
    for prev, next in replacements:
        output = output.replace(prev, next)
        output = output.replace(prev.capitalize(), next.capitalize())
    return output


@register.filter
def replace_dashes(s: str):
    return str(s).replace("-", "_")


@register.filter
def times(number):
    return range(number)


@register.filter
def short_id(s):
    s = str(s or "")
    if "-" in s:
        return s.split("-")[0]
    else:
        return s.split("_")[0]


@register.filter
def id_safe_str(s):
    return common.strip_non_alphanumeric(common.default_str(s))


@register.filter
def default_id(id_val):
    if common.is_valid_uuid(id_val):
        return id_val
    else:
        return str(common.UUID_NULL_OBJECT)


@register.filter
def join_ids(ids: List):
    return "|".join([o if isinstance(o, str) else str(o['id']) for o in common.ensure_list(ids)])


@register.filter
def remove_empty_lines(s: str):
    lines = common.safe_split(s, delimeter="\n", strip=True)
    lines = common.filter_empty(lines)
    return "\n".join(lines)


@register.filter
def html_safe_id(obj: models.Model):
    objid = str(common.get(obj, "id")).replace("-", "_")
    return f"{obj.__class__.__name__}__{objid}"


@register.filter
def label(s: str):
    s = s or ""
    return s.replace("_", " ").title()


@register.filter
def pprint_json(value):
    return mark_safe(json.dumps(value, indent=4))


@register.filter
def class_name(value):
    return value.__class__.__name__


@register.filter(name='endswith')
def endswith(value, arg):
    return value.endswith(arg)


@register.filter(name='startswith')
def startswith(value, arg):
    return value.startswith(arg)


@register.filter(name="not_eq")
def not_eq(v1, v2):
    return not eq(v1, v2)


@register.filter(name="eq")
def eq(v1, v2):
    return str(v1) == str(v2)


@register.filter(name="not")
def not_val(value):
    return not common.parse_bool(value)


@register.filter(name="sanitize_html")
def sanitize_html(value):
    """Remove potentially dangerous HTML before rendering."""
    return strip_tags(value or "")


@register.filter(name="strip_propose_change_markers")
def strip_propose_change_markers(value):
    """Remove [PROPOSE_CHANGE]...[/PROPOSE_CHANGE] markers from message content."""
    import re
    if not value:
        return value
    pattern = re.compile(r'\[PROPOSE_CHANGE\].*?\[/PROPOSE_CHANGE\]', re.DOTALL)
    return pattern.sub('', value).strip()


@register.filter(name="get_item")
def get_item(dictionary, key):
    """Get item from dictionary using key."""
    return dictionary.get(key, [])


@register.filter(name="format_json")
def format_json(value):
    """Format JSON object as pretty-printed string."""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, cls=ErieIronJSONEncoder)
