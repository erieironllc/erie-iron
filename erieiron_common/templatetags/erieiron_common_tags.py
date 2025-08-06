import glob
import json
import os
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

from erieiron_common import common, date_utils, settings_common
from erieiron_common.aws_utils import get_cloudwatch_url

register = template.Library()


@register.filter(name='highlight')
def highlight_code(code, lang='python'):
    try:
        lexer = get_lexer_by_name(lang, stripall=True)
        formatter = HtmlFormatter(linenos=True, cssclass="highlight")
        return mark_safe(highlight(code, lexer, formatter))
    except Exception as e:
        return code


@register.filter(name='markdown')
def markdown_format(text):
    return mark_safe(markdown.markdown(text))


@register.filter
def dictsort_case_insensitive(value, arg):
    try:
        return sorted(value, key=lambda x: x.get(arg, '').lower())
    except (AttributeError, TypeError):
        return value


@register.simple_tag
def timestamp_static(orig_filename):
    static_dir_root = Path.cwd() / settings_common.STATIC_COMPILED_DIR
    filename, ext = common.get_filename_and_extension(f"{static_dir_root}/{orig_filename}")
    
    files_with_time = []
    
    for file_path in glob.glob(os.path.join(static_dir_root, f"{filename}-*.{ext}")):
        base_name = os.path.basename(file_path)
        timestamp = base_name.split('-')[1].split('.')[0]
        file_time_tuple = (file_path, int(timestamp))
        files_with_time.append(file_time_tuple)
    
    files_with_time.sort(key=lambda x: x[1], reverse=True)
    
    latest_matching_file = orig_filename  # default to the orig name in the case it's not timestamped
    for idx, file_time_tuple in enumerate(files_with_time):
        file = file_time_tuple[0]
        if idx == 0:
            latest_matching_file = os.path.basename(file)
        else:
            common.quietly_delete(file)
    
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
def replace_dashes(s: str):
    return str(s).replace("-", "_")


@register.filter
def times(number):
    return range(number)


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
    return s.title().replace("_", " ")


@register.filter
def pprint_json(value):
    return json.dumps(value, indent=4)


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
