import threading
import time
from functools import wraps


def func_timer(func):
    def wrapper(*args, **kwargs):
        from erieiron_common import common
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        common.log_info(f"{func.__name__} execution time: {execution_time} seconds")
        return result

    return wrapper


def debugger(func):
    def wrapper(*args, **kwargs):
        from erieiron_common import common
        common.log_info(f"Calling {func.__name__} with args: {args} kwargs: {kwargs}")
        result = func(*args, **kwargs)
        common.log_info(f"{func.__name__} returned: {result}")
        return result

    return wrapper


def singleton(cls):
    instances = {}
    lock = threading.Lock()

    @wraps(cls)
    def get_instance(*args, **kwargs):
        with lock:
            if cls not in instances:
                instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return get_instance
