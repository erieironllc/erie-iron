import decimal
import json
import logging
import threading

threadLocal = threading.local()

TL_CACHE_MAP = 'cache_map'
TL_TO_PRESERVE_DURING_CLEAR = [
]


def reset_thread_locals():
    for k in dir(threadLocal):
        try:
            if k not in TL_TO_PRESERVE_DURING_CLEAR:
                delattr(threadLocal, k)
        except Exception:
            pass


def exists(key):
    from erieiron_common import models

    return models.CacheData.objects.filter(key=key).exists()


def data_get_json_list(keys):
    from . import models, common

    tl_json_key = _tl_json_key("list_key" + "~!~".join(keys))

    tl_val = tl_get(tl_json_key)
    if tl_val is not None:
        return tl_val

    s = "[%s]" % ", ".join(common.filter_empty([cd.val for cd in models.CacheData.objects.filter(key__in=keys)]))
    json_val = json.loads(s)
    tl_set(tl_json_key, json_val)

    return json_val


def data_get_json(key, default=None):
    if default is None:
        default = {}

    tl_val = tl_get(key)
    if tl_val is not None:
        return tl_val

    key_val = data_get(key, None)
    json_val = json.loads(str(key_val)) if key_val else default
    tl_set(key, json_val)

    return json_val


def data_set_json(key, json_val):
    if json_val is None:
        data_clear(key)
        return None
    else:
        tl_set(key, json_val)
        return data_set(
            key,
            json.dumps(json_val, ensure_ascii=True, default=default_converter)
        )


def default_converter(o):
    if isinstance(o, decimal.Decimal):
        return float(o)
    return o.__dict__


def data_clear(key):
    from . import models

    clear_thread_local(key)

    models.CacheData.objects.filter(key=key).delete()


def clear_thread_local(key):
    try:
        delattr(threadLocal, _tl_cache_key(key))
    except:
        pass
    return key


def data_set(key, val):
    from . import models

    tl_set(_tl_cache_key(key), val)

    try:
        cache_data = models.CacheData.objects.get(key=key)
    except models.CacheData.DoesNotExist:
        cache_data = models.CacheData(key)

    cache_data.val = val

    try:
        cache_data.save()
    except Exception as e:
        logging.exception(e)


def data_get(key, default=None):
    from . import models
    key = str(key)

    cache_key = _tl_cache_key(key)

    cache_data = tl_get(cache_key)
    if cache_data is None:
        try:
            tl_set(cache_key, models.CacheData.objects.get(key=key).val)
        except models.CacheData.DoesNotExist:
            tl_set(cache_key, None)

    cache_data = tl_get(cache_key)
    if cache_data is None:
        return default
    else:
        return cache_data


def tl_get(key, default=None):
    return getattr(threadLocal, key, default)


def tl_set(key, val):
    setattr(threadLocal, key, val)
    return val


def _tl_json_key(key):
    return "json_" + TL_CACHE_MAP + key


def _tl_cache_key(key):
    return TL_CACHE_MAP + key


def map_get(cache_key, map_key, default=None):
    from . import common
    return common.get(data_get_json(cache_key), map_key, default)
