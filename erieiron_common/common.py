import ast
import hashlib
import importlib
import ipaddress
import json
import logging
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import types
import unicodedata
import urllib.request
import uuid
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median
from typing import Tuple, List, Type, Optional
from urllib.parse import urlparse

import numpy as np
import psutil
import requests
from django.core.files.uploadedfile import UploadedFile, TemporaryUploadedFile, InMemoryUploadedFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Model, ForeignKey, OneToOneField, QuerySet
from django.db.models.fields.files import FieldFile
from django.utils import timezone as tz
from numpy import ndarray
from sklearn.metrics.pairwise import cosine_similarity

from erieiron_common import settings_common
from erieiron_common.json_encoder import ErieIronJSONEncoder

UUID_NULL_OBJECT = uuid.UUID('11111111-1111-1111-1111-111111111111')

COMMENT_CHAR_START_MAP = {
    '.py': '#',
    '.js': '//',
    '.java': '//',
    '.c': '//',
    '.cpp': '//',
    '.sh': '#',
    '.rb': '#',
    '.html': '<!-- ',
    '.xml': '<!-- ',
    '.sql': '--',
    '.yaml': '#',
    '.yml': '#',
    '.ini': ';',
    '.bat': 'REM',
    '.r': '#',
}

COMMENT_CHAR_END_MAP = {
    '.html': ' -->',
    '.xml': ' -->'
}


class HashableDict(dict):
    def __hash__(self):
        if hasattr(self, 'token'):
            return hash(getattr(self, 'token'))
        elif 'token' in self:
            return hash(self['token'])
        else:
            return hash(tuple(sorted(self.items())))


def comment_out_line(file_path_or_ext, line_str):
    file_path_or_ext = str(file_path_or_ext)
    ext = file_path_or_ext if file_path_or_ext.startswith('.') else Path(file_path_or_ext).suffix
    comment_start_char = COMMENT_CHAR_START_MAP.get(ext)
    if not comment_start_char:
        return line_str
    
    line_str = line_str or ""
    
    match = re.match(r'^(\s*)(.*)', line_str)
    leading_whitespace, stripped_text = match.groups()
    
    return f"{leading_whitespace}{comment_start_char} {stripped_text}{COMMENT_CHAR_END_MAP.get(ext, '')}"


def str_list(the_list):
    return [str(the_list) for the_list in ensure_list(the_list)]


def list_to_string(the_list, delim=" "):
    return delim.join(str_list(the_list))


def strip_non_numeric(s, replacement=""):
    return replacement.join([char for char in s if char.isdigit()])


def strip_non_alphanumeric(s, replacement=""):
    return re.sub(r'[^a-zA-Z0-9]', replacement, s)


def strip_non_alpha(s, replacement=""):
    return re.sub(r'[^a-zA-Z]', replacement, s)


def contains_any(val: str, test_vals: List[str], ignore_case=False) -> bool:
    val = default_str(val)
    test_vals = [default_str(v) for v in ensure_list(test_vals)]
    
    if ignore_case:
        val = val.lower()
        test_vals = [v.lower() for v in test_vals]
    
    return any(v in val for v in test_vals)


def get(o, key, default_val=None, check_attr=True):
    if isinstance(key, list):
        v = get(o, key[0], default_val, check_attr=check_attr)
        if len(key) > 1:
            return get(v, key[1:], default_val=default_val, check_attr=check_attr)
        else:
            return v
    else:
        if o is None:
            return default_val
        
        try:
            if has(o, key):
                return o[key]
        except:
            pass
        
        if check_attr:
            try:
                if hasattr(o, key):
                    return getattr(o, key, default_val)
            except:
                pass
        
        return default_val


def has(d, key):
    if d is None:
        return None
    
    return key in d and d[key] is not None


def trim_ndarray(arr: ndarray, length) -> ndarray:
    slices = tuple(slice(None, length) for _ in arr.shape)
    return arr[slices]


def get_size_in_mb(file):
    if isinstance(file, UploadedFile):
        return file.size / (1024 * 1024)
    elif isinstance(file, str) or isinstance(file, Path):
        return os.path.getsize(str(file)) / (1024 * 1024)
    else:
        return os.path.getsize(file.name) / (1024 * 1024)


def get_weighted_random_choice(items_ranked_descending_priority):
    items_ranked_descending_priority = ensure_list(items_ranked_descending_priority)
    
    if len(items_ranked_descending_priority) == 0:
        return None
    
    if len(items_ranked_descending_priority) == 1:
        return items_ranked_descending_priority[0]
    
    weights = []
    for idx, item in enumerate(items_ranked_descending_priority):
        weights.append((100 * len(items_ranked_descending_priority)) // (idx + 1))
    
    return random.choices(
        items_ranked_descending_priority,
        k=1,
        weights=weights
    )[0]


def ensure_list(v):
    if v is None:
        return []
    
    if isinstance(v, np.ndarray):
        return ensure_list(v.tolist())
    
    if isinstance(v, zip):
        return list(v)
    
    if is_list_like(v):
        return list(v)
    
    return [v]


def is_list_like(v):
    if isinstance(v, QuerySet):
        return True
    
    if isinstance(v, List):
        return True
    
    if isinstance(v, set):
        return True
    
    return False


def get_idx(vals, idx, default=None):
    vals = ensure_list(vals)
    idx = parse_int(idx)
    try:
        return vals[idx]
    except:
        return default


def get_first_or_none(the_list: List):
    the_list = ensure_list(the_list)
    if len(the_list) == 0:
        return None
    else:
        return the_list[0]


def percent_difference(x, y):
    if x == y:
        return 0  # If both numbers are the same, percent difference is 0
    return abs(x - y) / ((x + y) / 2) * 100


# def

def find_repeating_patterns(sequence, pattern_length):
    if pattern_length < 2 or pattern_length > len(sequence) // 2:
        raise Exception("Pattern length must be at least 2 and no more than half the length of the sequence.")
    
    # Dictionary to store patterns and their starting indices
    patterns = {}
    
    # Loop through the sequence to extract possible patterns
    for i in range(len(sequence) - pattern_length + 1):
        # Create a subsequence (pattern candidate)
        pattern = tuple(sequence[i:i + pattern_length])
        
        # Check if this pattern has occurred before
        if pattern in patterns:
            patterns[pattern].append(i)
        else:
            patterns[pattern] = [i]
    
    # Filter and return patterns that occur more than once
    return {pat: idxs for pat, idxs in patterns.items() if len(idxs) > 1}


def change_extension(file_path: str, new_extension) -> str:
    dir_name, file_name, _ = get_dir_filename_and_extension(file_path)
    return f"{dir_name}/{file_name}.{new_extension}"


def get_file_extension(file_path: str) -> str:
    return get_filename_and_extension(file_path)[1]


def get_dir_filename_and_extension(file_path: str) -> Tuple[str, str, str]:
    dir_name = os.path.dirname(file_path)
    file_name, extension = get_filename_and_extension(file_path)
    return dir_name, file_name, extension


def get_basename(file_path: str) -> str:
    return get_filename_and_extension(file_path)[0]


def get_filename_and_extension(file_path: str) -> Tuple[str, str]:
    path_basename = os.path.basename(file_path)
    parts = path_basename.split(".")
    
    extension = parts[-1].lower()
    name = path_basename[0:len(path_basename) - (len(extension) + 1)]
    
    return name, extension


def remove_questions(paragraph):
    # Split the paragraph into sentences using regex
    sentences = re.split(r'(?<=[.!?]) +', paragraph)
    
    # Filter out sentences that end with a question mark
    sentences_without_questions = [sentence for sentence in sentences if not sentence.strip().endswith('?')]
    
    # Join the remaining sentences back into a single paragraph
    modified_paragraph = ' '.join(sentences_without_questions)
    
    return modified_paragraph


def millis_to_hhmmss_trimmed(millis):
    seconds_unrounded = millis / 1000
    seconds = round(millis / 1000)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = seconds % 60
    if millis < 60 * 1000:
        return f"{seconds_unrounded:.1f}"
    elif millis < 60 * 60 * 1000:
        return f"{minutes}:{seconds:02}"
    else:
        return f"{hours}:{minutes:02}:{seconds:02}"


def format_millis_to_bars_short(time_ms, bpm, beats_per_measure):
    total_minutes = time_ms / 60000
    total_beats = bpm * total_minutes
    total_bars = 1 + int(total_beats // beats_per_measure)
    remainder_beats = 1 + int(total_beats % beats_per_measure)
    
    return f"{total_bars}:{remainder_beats}"


def format_millis_to_bars(time_ms, bpm, beats_per_measure):
    duration_per_beat = 60000 / bpm
    
    duration_per_measure = duration_per_beat * beats_per_measure
    bar_number = int(time_ms // duration_per_measure)
    time_into_measure = time_ms % duration_per_measure
    
    beat_number = int(round(time_into_measure / duration_per_beat, ndigits=0))
    
    if beat_number == beats_per_measure:
        bar_number += 1
        beat_number = 0
    
    if beat_number == 0:
        if bar_number == 1:
            return f"{bar_number} bar"
        else:
            return f"{bar_number} bars"
    else:
        return f"{bar_number}.{beat_number} bars"


def format_millis_to_hr_min_sec(millis, decimal_places=0):
    millis = int(millis)
    seconds = f"{float((millis / 1000) % 60):.{decimal_places}f}"
    minutes = int((millis // (1000 * 60)) % 60)
    hours = int((millis // (1000 * 60 * 60)) % 24)
    
    if millis < 60 * 1000:
        return f"{seconds} sec"
    elif millis < 60 * 60 * 1000:
        return f"{minutes} min {seconds} sec"
    else:  # 1 hour or more
        return f"{hours} hr {minutes} min {seconds} sec"


def millis_to_hhmmss(millis):
    seconds = millis / 1000
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:06.3f}"


def parse_int(v, min_max: Tuple[int, int] = None, default_val=None) -> int:
    if not is_numeric(v):
        return default_val
    
    i = int(float(v))
    
    if min_max:
        i = max(min_max[0], min(min_max[1], i))
    
    return i


def parse_bool(v) -> bool:
    if v is None:
        return False
    
    if isinstance(v, bool):
        return v
    
    if is_list_like(v) and len(v) == 0:
        return False
    
    s = str(v)
    if s.lower() in ['true', '1', 't', 'y', 'yes']:
        return True
    elif s.lower() in ['', 'false', '0', 'f', 'n', 'no']:
        return False
    raise ValueError(f"Cannot parse boolean value from: {s}")


def ensure_open_file(file):
    if isinstance(file, InMemoryUploadedFile):
        output_path = create_temp_file(f"{uuid.uuid4()}.{file.name}")
        with open(output_path, 'wb') as output_file:
            for chunk in file.chunks():
                output_file.write(chunk)
        return open(output_path, 'rb')
    
    if isinstance(file, str):
        return open(file, 'rb')
    elif file.closed:
        if isinstance(file, TemporaryUploadedFile):
            return open(file.temporary_file_path(), 'rb')
        else:
            return open(file.name, 'rb')
    
    return file


def get_filename(file):
    if isinstance(file, str):
        return file
    else:
        return file.name


def quietly_delete(file):
    if not file:
        return
    
    if is_list_like(file):
        for f in file:
            quietly_delete(f)
        return
    
    if isinstance(file, str) or isinstance(file, Path):
        file_path = file
    else:
        try:
            file.close()
        except:
            pass
        file_path = file.name
    
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            log_info(f"unable to delete {file_path}:  {e}")


def delete_dir(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)


def is_not_empty(v):
    return not is_empty(v)


def is_empty(v):
    return v is None or len(str(v)) == 0


def print_working_dir_files():
    current_directory = os.getcwd()
    for file in [file for file in os.listdir(current_directory) if
                 os.path.isfile(os.path.join(current_directory, file))]:
        log_info(file)
    start_path = '.'
    for root, dirs, files in os.walk(start_path):
        levels_deep = len(root.split(os.sep))
        basename = os.path.basename(root)
        
        if len(basename) == 0 or basename == "conf" or basename == "erieiron_common":
            print((levels_deep - 1) * '---', basename)
            for file in files:
                log_info(levels_deep * '---', file)


def filter_empty(data_list, to_none=False):
    if data_list is None:
        return None
    
    l2 = [li for li in ensure_list(data_list) if is_not_empty(li)]
    if to_none and len(l2) == 0:
        return None
    else:
        return l2


def filter_none(data_list):
    if data_list is None:
        return []
    return [li for li in ensure_list(data_list) if li is not None]


def default(obj, default_val=None):
    if obj is None:
        return default_val
    else:
        return obj


def default_str(s, default_val=""):
    if s is None:
        return default_val
    
    s = str(s)
    if is_empty(s):
        return default_val
    else:
        return s


def safe_strs(string_list):
    return [safe_str(s) for s in ensure_list(string_list)]


def safe_str(s, default_val=None):
    if s is None:
        return default_val
    try:
        return str(s).replace('\x00', '')
    except:
        return str(unicodedata.normalize('NFKD', s).encode('ascii', 'ignore')).replace('\x00', '')


def safe_median(vals):
    if len(vals) == 0:
        return 0
    else:
        return median(vals)


def safe_min(vals, default_val=0):
    vals = filter_none(vals)
    if len(vals) == 0:
        return default_val
    else:
        return min(vals)


def safe_max(vals, default_val=0, force_int=False):
    vals = filter_none(vals)
    if len(vals) == 0:
        return default_val
    else:
        if force_int:
            return max([int(v) for v in vals])
        else:
            return max([v for v in vals])


def safe_avg(vals, min_val=None):
    return safe_divide(sum(vals), len(vals), min_val=min_val)


def last(vals):
    vals = filter_none(vals)
    if len(vals) > 0:
        return vals[-1]
    else:
        return None


def flatten(items) -> list:
    def _flatten(items):
        for item in items:
            if isinstance(item, (list, tuple)):
                yield from _flatten(item)
            elif is_list_like(item):
                yield from _flatten(item)
            else:
                yield item
    
    return list(_flatten(items))


def first(vals):
    vals = filter_none(ensure_list(vals))
    if len(vals) > 0:
        return vals[0]
    else:
        return None


def get_unique_values(data_dict):
    values = []
    for k, val_list in data_dict.items():
        values += val_list
    return list(set(filter_empty(values)))


def struct_start(path_keys, leaf_value):
    d = {}
    struct_set(d, path_keys, leaf_value)
    return d


def struct_set(struct, path_keys, leaf_value):
    path_keys = ensure_list(path_keys)
    struct_node = struct
    for idx, k in enumerate(path_keys):
        last_key = (idx == len(path_keys) - 1)
        if k not in struct_node:
            if last_key:
                struct_node[k] = leaf_value
            else:
                new_struct_node = struct_node[k] = {}
                struct_node = new_struct_node
        else:
            if last_key:
                struct_node[k] = leaf_value
            else:
                new_struct_node = struct_node[k]
                struct_node = new_struct_node


def struct_append(struct, path_keys, leaf_value):
    struct_node = struct
    for idx, k in enumerate(path_keys):
        last_key = (idx == len(path_keys) - 1)
        if k not in struct_node:
            if last_key:
                struct_node[k] = [leaf_value]
            else:
                new_struct_node = struct_node[k] = {}
                struct_node = new_struct_node
        else:
            if last_key:
                struct_node[k].append(leaf_value)
            else:
                new_struct_node = struct_node[k]
                struct_node = new_struct_node


def struct_add(struct, path_keys, leaf_value, max_value=None):
    struct_node = struct
    for idx, k in enumerate(path_keys):
        last_key = (idx == len(path_keys) - 1)
        if k not in struct_node:
            if last_key:
                struct_node[k] = safe_min([max_value, leaf_value])
            else:
                new_struct_node = struct_node[k] = {}
                struct_node = new_struct_node
        else:
            if last_key:
                if is_numeric(leaf_value):
                    struct_node[k] += leaf_value
                else:
                    struct_node[k] = leaf_value
                struct_node[k] = safe_min([max_value, struct_node[k]])
            else:
                new_struct_node = struct_node[k]
                struct_node = new_struct_node


def safe_sum(vals, default_value=0):
    if vals is None:
        return default_value
    
    vals = filter_none([ensure_numeric(v) for v in vals])
    if len(vals) == 0:
        return default_value
    
    return sum(vals)


def safe_divide(top, bottom, min_val=0):
    if top is None or bottom is None:
        return min_val
    
    if bottom == 0:
        v = 0
    else:
        v = float(top) / float(bottom)
    
    if min_val:
        return max(min_val, v)
    else:
        return v


def safe_split(s, delimeter: str = ",", strip=True, lower=False):
    s = default_str(s)
    if isinstance(delimeter, list):
        for d in delimeter:
            s = replace_case_insensitive(s, d, ",")
        return safe_split(s, ",", strip)
    
    if is_empty(s):
        return []
    
    vals = []
    for s1 in s.split(delimeter):
        s1 = default_str(s1)
        if strip:
            s1 = s1.strip()
        if lower:
            s1 = s1.lower()
        vals.append(s1)
    return vals


def ensure_numeric(s):
    try:
        return float(s)
    except:
        return None


def is_numeric(s):
    try:
        float(s)
        return True
    except:
        return False


def replace_case_insensitive(look_in, look_for, replace_with):
    if look_in is None:
        return None
    
    if look_for is None or replace_with is None:
        return look_in
    
    return re.compile(re.escape(look_for), re.IGNORECASE).sub(replace_with, look_in)


def replace_nonalpha(s, replace_with):
    return re.compile('[^a-zA-Z]').sub(replace_with, s)


def split_camel_case(s):
    if s is None:
        return []
    
    return re.sub('([a-z])([A-Z])', r'\1 \2', s).split()


def diff_dict(dict1, dict2):
    for k in dict1:
        if sorted(get(dict1, k, [])) != sorted(get(dict2, k, [])):
            log_info('----')
            log_info(k, sorted(get(dict1, k, [])), sorted(get(dict2, k, [])))
    return dict1 == dict2


def hours_diff(d1, d2=None):
    if d2 is None:
        d2 = datetime.now()
    
    diff = d2 - d1
    
    days, seconds = diff.days, diff.seconds
    hours = days * 24 + seconds // 3600
    
    return math.ceil(hours)


def get_checksum(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_recent_vals(vals, count):
    if len(vals) < count:
        return vals
    
    out = []
    rl = list(vals)
    rl.reverse()
    rl.pop()
    for w in rl:
        out.append(w)
        if len(out) == count:
            break
    
    return out


def base_round(x, base=5):
    return int(base * round(float(x) / base))


def is_json_serializable(value):
    try:
        json.dumps(value, cls=DjangoJSONEncoder)
        return True
    except (TypeError, ValueError):
        return False


def model_to_dict(instance):
    if not isinstance(instance, Model):
        raise ValueError("Expected a Django model instance")
    
    data = {
        "cls_name": instance.__class__.__name__
    }
    
    for field in instance._meta.get_fields():
        field_name = field.name
        
        if not hasattr(instance, field_name):
            continue
        
        field_value = getattr(instance, field_name)
        if isinstance(field_value, FieldFile):
            continue
        
        if isinstance(field, (ForeignKey, OneToOneField)):
            field_value = field_value.pk if field_value else None
        
        if is_json_serializable(field_value):
            data[field_name] = field_value
    
    return data


def model_to_dict_s(model):
    if not model:
        return ""
    else:
        return json.dumps(model_to_dict(model), cls=DjangoJSONEncoder, indent=4)


def get_file_name(file_path):
    base_name = os.path.basename(file_path)
    file_name = os.path.splitext(base_name)[0]
    return file_name


def sanitize_filename(filename):
    illegal_chars = r'<>:"/\|?*'
    pattern = f"[{re.escape(illegal_chars)}]"
    return re.sub(pattern, "_", filename).replace(" ", "_")


def copy_to_temp_file(source_file_path):
    if not os.path.isfile(source_file_path):
        raise FileNotFoundError(f"Source file '{source_file_path}' does not exist")
    
    if not os.access(source_file_path, os.R_OK):
        raise PermissionError(f"No read permission for source file: {source_file_path}")
    
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file_path = temp_file.name
        
        with open(source_file_path, 'rb') as src_file:
            data = src_file.read()
        
        with open(temp_file_path, 'wb') as dst_file:
            dst_file.write(data)
        
        return Path(temp_file_path)
    
    except Exception as e:
        try:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
        except:
            pass
        
        raise IOError(f"Error copying file: {e}")


def move_with_overwrite(source_file, dest_file):
    try:
        quietly_delete(dest_file)
    except:
        pass
    shutil.move(source_file, dest_file)
    
    return dest_file


def get_dict(obj):
    if obj is None:
        return None
    
    if is_list_like(obj):
        return [get_dict(obj2) for obj2 in obj]
    
    d = None
    
    if isinstance(obj, dict):
        d = obj
    
    if d is None and hasattr(obj, 'to_dict'):
        d = obj.to_dict()
    elif d is None:
        d = {
            k: v for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    
    d["cls_name"] = obj.__class__.__name__
    
    return d


def string_to_number(s):
    base = 27  # 26 letters + 1 (since we're starting from 1)
    num = 0
    for char in s.lower():
        if 'a' <= char <= 'z':
            value = ord(char) - ord('a') + 1
        else:
            value = 0  # Non-alphabet characters are treated as zero
        num = num * base + value
    return num


def read_file(file_path, max_lines=None):
    with open(file_path, 'r', encoding="utf-8", errors="ignore") as file:
        filtered_lines = file.readlines()
        
        if max_lines:
            filtered_lines = filtered_lines[-max_lines:]
        
        return "\n".join(filtered_lines)


def read_json(file_name, default=None):
    if not file_name:
        return default
    
    if not os.path.exists(file_name):
        return default
    
    with open(file_name, "r") as f:
        return json.load(f)


def write_json_to_tempfile(json_data):
    with tempfile.NamedTemporaryFile(mode="w", suffix=f".json", delete=False) as file:
        json.dump(json_data, file, indent=4)
    return file


def write_json(file_name, json_data, indent=4):
    with open(file_name, "w") as f:
        if indent:
            json.dump(json_data, f, indent=indent, cls=ErieIronJSONEncoder)
        else:
            json.dump(json_data, f, separators=(",", ":"), cls=ErieIronJSONEncoder)
    return json_data


def remove_duplicates_from_dict(d1):
    d2 = {}
    for k, items in d1.items():
        d2[k] = list(OrderedDict.fromkeys(items))
    return d2


def date_to_epoch_ms(target_date):
    """
    Convert a date object to epoch milliseconds at the start and end of the day.

    Args:
        target_date (date): The target date.

    Returns:
        tuple: (start_epoch_ms, end_epoch_ms)
    """
    # Start of the day (00:00:00)
    from datetime import time
    start_datetime = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    start_epoch_ms = int(start_datetime.timestamp() * 1000)
    
    # End of the day (23:59:59.999999)
    end_datetime = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)
    end_epoch_ms = int(end_datetime.timestamp() * 1000)
    
    return start_epoch_ms, end_epoch_ms


def get_page_pathname(url_str, count_parts=1):
    parsed_url = urlparse(url_str)
    interesting_parts = []
    for path_part in parsed_url.path.split('/'):
        path_part_stripped = path_part.strip()
        
        if not path_part_stripped:
            continue
        
        try:
            person_uuid = uuid.UUID(path_part_stripped)
            continue
        except:
            pass
        
        if is_numeric(path_part_stripped):
            continue
        
        interesting_parts.append(path_part_stripped)
    
    if len(interesting_parts) > 0:
        path = "/".join(interesting_parts[-1 * count_parts:])
    else:
        # root is the project view
        path = "project"
    
    return path


def get_path(path_str) -> Path:
    if isinstance(path_str, Path):
        return path_str
    
    if "~" in path_str:
        path_str = os.path.expanduser(path_str)
    
    path_str = Path(path_str)
    if not path_str.exists():
        raise Exception(f"{path_str} does not exist")
    
    return path_str


def log_debug(*args):
    s = None
    
    if len(args) == 0:
        return None
    elif len(args) == 1:
        s = args[0]
    elif len(args) > 1:
        s = "\t".join([str(a) for a in args])
    
    if s is None or s == "None":
        logging.debug("log_info called with None")
        logging.error(traceback.format_exc())
    
    logging.debug(s)
    return s


def log_distinct(s: str):
    log_info(f"\n\n\n{s}\n\n\n")


def log_info(*args):
    msg = wrap_log_message_in_contenxt(args)
    logging.info(msg)
    return msg


def log_error(*args):
    msg = wrap_log_message_in_contenxt(args)
    logging.error(msg)
    return msg


def dict_to_vars(the_dict, *args):
    vals = [get(the_dict, a) for a in args]
    return vals


def wrap_log_message_in_contenxt(args):
    s = None
    
    if len(args) == 0:
        return None
    elif len(args) == 1:
        s = args[0]
    elif len(args) > 1:
        s = "\t".join([str(a) for a in args])
    
    if s is None or s == "None":
        print("log_info called with None")
        logging.error(traceback.format_exc())
    
    thread_name = get_current_thread_name()
    
    # return f"{get_memory_used_percent()}m {get_cpu_used_percent()}c {thread_name} {s}"
    return f"{thread_name} {get_memory_used_percent()}% {s}"


def get_current_thread_name():
    return threading.current_thread().name.split("-")[-1]


def get_cpu_used_percent() -> int:
    cpu_percentages = psutil.cpu_percent(interval=1, percpu=True)
    used_percent = int(sum(cpu_percentages) / len(cpu_percentages))
    return used_percent


def get_memory_used_percent() -> int:
    memory_info = psutil.virtual_memory()
    percent_used_mem = int(memory_info.used * 100 // memory_info.total)
    return percent_used_mem


def get_machine_name():
    try:
        # this is the ecs/docker metadata endpoint
        response = requests.get('http://169.254.169.254/latest/meta-data/instance-id', timeout=1)
        response.raise_for_status()
        return response.text
    except:
        # thank you chatgpt!
        mac_address = ':'.join(f'{(uuid.getnode() >> i) & 0xff:02x}' for i in range(0, 48, 8))
        return f"{platform.node()}-{mac_address}"


def is_valid_uuid(s):
    if not s:
        return False
    
    if str(s) == str(UUID_NULL_OBJECT):
        return False
    
    if isinstance(s, uuid.UUID):
        return True
    
    try:
        uuid_s = uuid.UUID(s)
        return str(uuid_s) == str(s)
    except:
        return False


def join_with_and(items):
    if not items:
        return ''
    elif len(items) == 1:
        return items[0]
    elif len(items) == 2:
        return ' and '.join(items)
    else:
        return ', '.join(items[:-1]) + ' and ' + items[-1]


def download_file(url) -> Path:
    response = requests.get(url, stream=True)
    if response.status_code != 200:
        raise Exception(f"Failed to download {url}. HTTP Status Code: {response.status_code}")
    
    total_size = int(response.headers.get('Content-Length', 0))
    chunk_size = 8192
    num_chunks = total_size // chunk_size + (1 if total_size % chunk_size != 0 else 0)
    
    downloaded = 0
    
    with tempfile.NamedTemporaryFile(mode="wb", suffix=f".{url.split('.')[-1]}", delete=False) as file:
        output_file = Path(file.name)
        log_info(f"Starting download... Total size: {bytes_to_megabytes(total_size)} bytes ({num_chunks} chunks) to {output_file}")
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:  # Filter out keep-alive new chunks
                file.write(chunk)
                downloaded += len(chunk)
                progress = (downloaded / total_size) * 100
                log_info(f"Progress: {progress:.2f}% {url} to {output_file}")
    
    log_info(f"\nFile downloaded successfully and saved to {output_file}")
    return output_file


def bytes_to_megabytes(bytes_value):
    return bytes_value / (1024 * 1024)


def find_closest_number(numbers, target, default_val=0):
    if not numbers:
        return default_val
    
    return min(numbers, key=lambda num: abs(num - target))


def percent_iterator(item_count: float):
    percent_per_item = 100 / item_count
    
    running_total = 0
    for i in range(int(item_count * 2)):
        running_total += percent_per_item
        
        if running_total < 100:
            yield percent_per_item
        else:
            percent_over = running_total - 100
            yield percent_per_item - percent_over
            break


def is_not_equivalent(value1, value2):
    return not is_equivalent(value1, value2)


def is_equivalent(value1, value2):
    try:
        return float(value1) == float(value2)
    except (ValueError, TypeError):
        return str(value1) == str(value2)


def get_local_server_cache():
    from django.core.cache import caches
    return caches['local_server_cache']


def is_within_percent(val1, val2, difference_range=0.1):
    return abs(val1 - val2) <= 0.1 * val2


def short_uuid(long_uuid: uuid.UUID) -> str:
    if not long_uuid:
        return ""
    else:
        return str(long_uuid).split("-")[-1]


def get_minutes_ago(mins):
    return get_now() - timedelta(minutes=mins)


def get_now():
    return tz.now()


def is_process_alive(pid):
    pid = int(pid)
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def kill_pid(pid, wait=None):
    pid = int(pid)
    try:
        process = psutil.Process(pid)
        process.terminate()  # Graceful termination
        if wait:
            process.wait(timeout=3)  # Wait for the process to terminate
        print(f"Process {pid} terminated.")
    except psutil.NoSuchProcess:
        pass


def create_temp_file(prefix: str, extension: str = None) -> Path:
    prefix = default_str(prefix)
    extension = default_str(extension)
    
    if extension:
        if not extension.startswith('.'):
            extension = '.' + extension
    elif "." in prefix:
        extension = prefix.split(".")[-1]
        prefix = ".".join(prefix.split(".")[:-1])
    else:
        extension = ""
    
    temp_file = tempfile.NamedTemporaryFile(prefix=prefix, suffix=extension, delete=False)
    temp_file_path = temp_file.name
    temp_file.close()
    
    return Path(temp_file_path)


def get_disk_used_percent():
    disk_usage = psutil.disk_usage('/')
    
    return 100 * disk_usage.free // disk_usage.total


def get_pids_by_command(command):
    pids = []
    for proc in psutil.process_iter(attrs=['pid', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if not cmdline:
                continue
            
            if command in " ".join(cmdline):
                pids.append(proc.info['pid'])
        
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    
    return pids


def serialize_class(cls: Type) -> str:
    return f"{cls.__module__}.{cls.__name__}"


def deserialize_class(serialized_class_and_module) -> Type:
    module_name, class_name = serialized_class_and_module.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def get_stack_trace_as_string(exception: Exception) -> str:
    return "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))


def empty_directory(directory):
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        try:
            # Remove file or symbolic link
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            # Remove a directory and all its contents
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')


def create_tgz(source_dir: Path, output_filename: Path = None) -> Path:
    source_dir = Path(source_dir)
    assert source_dir.exists()
    source_dir = str(source_dir)
    
    if output_filename is None:
        output_filename = create_temp_file(os.path.basename(source_dir), "tgz")
    
    with tarfile.open(output_filename, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))
    
    return Path(output_filename)


def import_module_from_path(module_file_path):
    base_module = get_base_module(module_file_path)
    module_file_path = os.path.abspath(module_file_path)
    module_name = f"{base_module}.{os.path.splitext(os.path.basename(module_file_path))[0]}"
    
    # noinspection PyUnresolvedReferences
    spec = importlib.util.spec_from_file_location(module_name, module_file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {module_file_path}")
    
    # noinspection PyUnresolvedReferences
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    
    return module


def import_module_from_string(code_str: str, module_name: str = "dynamic_module") -> types.ModuleType:
    """
    Dynamically imports a Python module from a string of code.

    Args:
        code_str (str): Python code to execute.
        module_name (str): Optional name to assign to the module.

    Returns:
        types.ModuleType: The dynamically created module object.
    """
    module = types.ModuleType(module_name)
    exec(code_str, module.__dict__)
    sys.modules[module_name] = module
    return module


def get_base_module(module_file_path):
    module_file_path = str(module_file_path)
    while module_file_path[0] in [".", "/"]:
        module_file_path = module_file_path[1:]
    
    return ".".join(module_file_path.split("/")[:-1])


def get_methods_with_decorator(decorator_name):
    decorator_name = decorator_name.replace("@", "")
    methods_to_call = []
    
    directory = os.getcwd()
    
    for root, dirs, files in os.walk(directory):
        # Skip virtual environment directories
        dirs[:] = [d for d in dirs if d not in ("env", "venv", "__pycache__")]
        
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                module_name = os.path.splitext(os.path.relpath(file_path, directory))[0].replace(os.path.sep, ".")
                
                with open(file_path, "r", encoding="utf-8") as f:
                    try:
                        tree = ast.parse(f.read(), filename=file_path)
                    except SyntaxError as e:
                        print(f"Skipping {file_path} due to syntax error: {e}")
                        continue
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):  # Check for function definitions
                        for decorator in node.decorator_list:
                            if isinstance(decorator, ast.Name) and decorator.id == decorator_name:
                                methods_to_call.append((module_name, node.name))
    
    methods = []
    for module_name, method_name in methods_to_call:
        try:
            module_path = os.path.join(os.getcwd(), module_name.replace(".", os.path.sep) + ".py")
            
            # noinspection PyUnresolvedReferences
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            
            # noinspection PyUnresolvedReferences
            module = importlib.util.module_from_spec(spec)
            
            spec.loader.exec_module(module)
            
            # Get the method and call it
            if hasattr(module, method_name):
                method = getattr(module, method_name)
                methods.append(method)
            else:
                logging.error(f"Method {method_name} not found in {module_name}")
        except Exception as e:
            logging.exception(f"Error calling {method_name} in {module_name}: {e}")
    
    return methods


def id_or(m: Model, default_val=None) -> Optional[uuid.UUID]:
    return m.id if m else default_val


def simulate_crash(wait_secs=0):
    if not settings_common.DEBUG:
        logging.error("attempting to simulate a crash in non DEBUG mode.  skipping it")
        return
    
    def delayed_exit():
        time.sleep(wait_secs)
        # noinspection PyUnresolvedReferences,PyProtectedMember
        os._exit(1)
    
    threading.Thread(target=delayed_exit, daemon=True).start()


def scp_from_host(username: str, host: str, source: str) -> Path:
    f = create_temp_file(
        f"training_results{datetime.now().strftime('%Y%m%d%M%S')}",
        "log"
    )
    
    command = ['scp', f'{username}@{host}:{source}', str(f)]
    
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Error transferring file: {result.stderr}")
    
    return f


def render_template(template_name, context_dict):
    from django.template import Engine
    from django.template import Context
    
    engine = Engine.get_default()
    template = engine.get_template(template_name)
    
    return template.render(Context(context_dict))


def cosine_sim(mel1, mel2):
    mel1_flat = mel1.flatten().reshape(1, -1)
    mel2_flat = mel2.flatten().reshape(1, -1)
    return cosine_similarity(mel1_flat, mel2_flat)[0, 0]


def invalid_file(path: Path) -> bool:
    return not valid_file(path)

def valid_file(path: Path) -> bool:
    try:
        assert_exists(path)
        return True
    except:
        return False


def assert_exists(path: Path) -> Path:
    if path is None:
        raise Exception(f"path {path} is None")
    
    p = Path(path).expanduser()
    if not p.exists():
        raise Exception(f"{path} does not exist")
    return p


def iterate_files_deep(
        root_directory: Path,
        respect_git_ignore=True,
        file_extensions: list[str] = None,
        gitignore_patterns: list[str] = None
):
    root_directory = assert_exists(root_directory)
    
    if not root_directory.is_dir():
        raise Exception(f"Path is not a directory: {root_directory}")
    
    gitignore_patterns = ensure_list(gitignore_patterns)
    gitignore_patterns.append(".git/")
    
    if respect_git_ignore:
        gitignore_file = root_directory / ".gitignore"
        if gitignore_file.exists():
            with open(gitignore_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith('#'):
                        # Convert gitignore pattern to regex pattern
                        pattern = _gitignore_to_regex(line)
                        gitignore_patterns.append(pattern)
    
    for file_path in root_directory.rglob("*"):
        if file_path.is_file():
            relative_path = file_path.relative_to(root_directory)
            relative_path_str = str(relative_path).replace('\\', '/')
            
            # Check if file matches any gitignore pattern
            if gitignore_patterns:
                if str(relative_path) == ".gitignore":
                    continue
                
                if any(re.match(pattern, relative_path_str) or
                       re.match(pattern, relative_path_str + '/') for pattern in gitignore_patterns):
                    continue
            
            # Filter by file extensions if specified
            if file_extensions is not None:
                file_extension = relative_path.suffix.lower()
                file_name = relative_path.name
                
                # Check if file matches any of the specified extensions or patterns
                matches = False
                
                for ext in file_extensions:
                    # Special case for Dockerfile pattern
                    if ext == "Dockerfile" and file_name.startswith("Dockerfile"):
                        matches = True
                        break
                    # Regular extension matching
                    else:
                        normalized_ext = ext if ext.startswith('.') else f'.{ext}'
                        if file_extension == normalized_ext.lower():
                            matches = True
                            break
                
                if not matches:
                    continue
            
            yield relative_path


def _gitignore_to_regex(gitignore_pattern):
    """Convert a gitignore pattern to a regex pattern."""
    # Escape special regex characters except for *, ?, and []
    pattern = re.escape(gitignore_pattern)
    
    # Replace escaped wildcards with regex equivalents
    pattern = pattern.replace(r'\*\*', '.*')  # ** matches any number of directories
    pattern = pattern.replace(r'\*', '[^/]*')  # * matches anything except /
    pattern = pattern.replace(r'\?', '[^/]')  # ? matches any single character except /
    
    # Handle directory patterns (ending with /)
    if gitignore_pattern.endswith('/'):
        pattern = pattern[:-1] + '(/.*)?$'  # Remove escaped / and add directory match
    else:
        pattern = pattern + '(/.*)?$'  # Match file or directory
    
    # Handle patterns starting with /
    if gitignore_pattern.startswith('/'):
        pattern = '^' + pattern[2:]  # Remove escaped / and anchor to start
    else:
        pattern = '(^|.*/)' + pattern  # Match anywhere in path
    
    return pattern


def xml_to_json(xml_string):
    import xml.etree.ElementTree as ET
    import json
    
    def recurse(node):
        result = {}
        # add attributes
        result.update(node.attrib)
        # add children
        for child in node:
            child_result = recurse(child)
            if child.tag in result:
                if isinstance(result[child.tag], list):
                    result[child.tag].append(child_result)
                else:
                    result[child.tag] = [result[child.tag], child_result]
            else:
                result[child.tag] = child_result
        # add text
        text = node.text.strip() if node.text else ''
        if text and not result:
            return text
        if text:
            result['text'] = text
        return result
    
    root = ET.fromstring(xml_string)
    return json.dumps({root.tag: recurse(root)}, indent=2)


def execute_management_cmd(command, output_file: Path = None) -> int:
    python_executable = os.path.join("env", "bin", "python")
    full_command = f"{python_executable} manage.py {command}"
    
    return exec_cmd(full_command, output_file)


def exec_cmd(full_command, output_file=None, cwd=None) -> int:
    def set_death_signal():
        if sys.platform.startswith('linux'):
            try:
                import ctypes
                import signal
                libc = ctypes.CDLL("libc.so.6")
                PR_SET_PDEATHSIG = 1
                libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
            except Exception as e:
                logging.error("Failed to set death signal: %s", e)
    
    if output_file:
        print(f'''about to execute "{full_command}". sending log to: 
tail -f {os.path.abspath(output_file)}

''')
        with open(output_file, "w") as outfile:
            process = subprocess.Popen(
                full_command,
                shell=True,
                cwd=cwd or os.getcwd(),
                stdout=outfile,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=set_death_signal
            )
            process.wait()
        return process.returncode
    else:
        print(f'about to execute "{full_command}". sending log to sysout')
        process = subprocess.Popen(
            full_command,
            shell=True,
            stdout=sys.stdout,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=set_death_signal
        )
        process.wait()
        return process.returncode


def assert_in_sandbox(sandbox_root_dir, file_path) -> Path:
    if is_running_in_container():
        # if we are running in the container, we are by default running in the sandbox
        return file_path
    
    file_path = Path(file_path).resolve()
    sandbox_root_dir = Path(sandbox_root_dir).resolve()
    if not os.path.abspath(file_path).startswith(os.path.abspath(sandbox_root_dir)):
        raise ValueError(f"file_path {os.path.abspath(file_path)} is not within sandbox_root_dir {os.path.abspath(sandbox_root_dir)}")
    return file_path


def is_running_in_container() -> bool:
    if os.getenv("RUNNING_IN_CONTAINER") == "true":
        return True
    try:
        with open('/proc/1/cgroup', 'rt') as f:
            return 'docker' in f.read()
    except:
        return False


def safe_filename(s, replacement="_", max_length=255):
    # Remove any character that is not alphanumeric, dot, dash, or underscore
    safe = re.sub(r'[^a-zA-Z0-9.\-_]', replacement, s)
    return safe[:max_length]


def build_absolute_uri(page=""):
    return f"{settings_common.BASE_URL}/{page}"


def copy_missing_files(src_dir, destination_dir):
    # Create destination directory if it doesn't exist
    destination_dir.mkdir(parents=True, exist_ok=True)
    # Copy files that don't exist, preserving existing files
    
    copied_files = []
    skipped_files = []
    for source_file in src_dir.rglob('*'):
        if source_file.is_file():
            # Calculate relative path from template root
            relative_path = source_file.relative_to(src_dir)
            dest_file = destination_dir / relative_path
            
            # Only copy if destination file doesn't exist
            if not dest_file.exists():
                # Create parent directories if needed
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                # Copy the file
                shutil.copy2(source_file, dest_file)
                copied_files.append(str(relative_path))
            else:
                skipped_files.append(str(relative_path))
    if copied_files:
        print(f"[✅] Copied {len(copied_files)} new files to: {destination_dir}")
        for file in copied_files[:5]:  # Show first 5 files
            print(f"    ├── {file}")
        if len(copied_files) > 5:
            print(f"    └── ... and {len(copied_files) - 5} more files")
    if skipped_files:
        print(f"[⏭️] Skipped {len(skipped_files)} existing files (preserved)")


def replace_in_file(the_file: Path, replacements: list[tuple[str, str]]):
    the_file = assert_exists(the_file)
    text = the_file.read_text()
    
    for look_for, replace_with in replacements:
        text = text.replace(look_for, replace_with)
    
    the_file.write_text(text)
    return the_file


def strings(l):
    return [str(s) for s in ensure_list(l)]


def safe_join(string_list, delim=" "):
    return str(delim).join(strings(string_list))


def run_cmd(cwd: Path, cmd: list[str]) -> subprocess.CompletedProcess:
    result = None
    
    try:
        env = os.environ.copy()
        result = subprocess.run(
            ensure_list(cmd),
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"stdout:\n{e.stdout.strip()}")
        print(f"stderr:\n{e.stderr.strip()}\n\ncwd:\n{cwd}\ncmd:\n{safe_join(cmd)}")
        raise Exception(
            f"Command failed: {safe_join(cmd)}\n"
            f"stdout:\n{e.stdout.strip()}\n"
            f"stderr:\n{e.stderr.strip()}"
        )
    except Exception as e:
        logging.info(result.stdout)
        logging.info(result.stderr)
        raise e
    else:
        logging.info(result.stdout)
        logging.info(result.stderr)
    
    return result


def truncate_text_lines(text_blob: str) -> str:
    if not text_blob:
        return text_blob
    
    processed_lines = []
    for line in str(text_blob).splitlines():
        if len(line) > 1000:
            line = f"[TRUNCATED] {line[:300]}...[{len(line)} chars]...{line[-100:]}"
        processed_lines.append(line)
    
    return "\n".join(processed_lines)


def random_string(length=16):
    import string
    import secrets
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def assert_not_empty(s):
    if not s:
        raise ValueError("value is empty")


def get_ip_address():
    ip_txt = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5).read().decode().strip()
    return f"{ipaddress.ip_address(ip_txt)}/32"
