from typing import Tuple

from django.db import connection

from erieiron_common import common
from erieiron_common.enums import SystemCapacity


def get_system_capacity(max_percent_capacity_consumed=70) -> Tuple[SystemCapacity, str]:
    mem_used = common.get_memory_used_percent()
    if mem_used > max_percent_capacity_consumed:
        return SystemCapacity.OVERLOAD, f"memory: {mem_used}% is greater than {max_percent_capacity_consumed}%"
    elif mem_used > (max_percent_capacity_consumed * .8):
        return SystemCapacity.CAPPED, f"memory: {mem_used}% is greater than {max_percent_capacity_consumed * .8}%"

    cpu_used = common.get_cpu_used_percent()
    if cpu_used > max_percent_capacity_consumed:
        return SystemCapacity.OVERLOAD, f"cpu: {cpu_used}% is greater than {max_percent_capacity_consumed}%"
    elif cpu_used > max_percent_capacity_consumed * .8:
        return SystemCapacity.CAPPED, f"cpu avail {cpu_used}% is greater than {max_percent_capacity_consumed * .8}%"

    if connection.vendor == 'postgresql':
        db_capacity, explanation = get_db_capacity()
        if SystemCapacity.AVAILABLE.neq(db_capacity):
            return db_capacity, explanation

    return SystemCapacity.AVAILABLE, ""


def get_db_capacity(max_percent_capacity_consumed=70) -> Tuple[SystemCapacity, str]:
    try:
        max_connections, used_connections = get_db_connections_info()
    except Exception as e:
        # if this throws, something is up with the DB.  consider us in overload state
        return SystemCapacity.OVERLOAD, f"exception while checking db connection status {e}"

    db_conns_used_percent = (used_connections / max_connections) * 100
    if db_conns_used_percent > max_percent_capacity_consumed:
        return SystemCapacity.OVERLOAD, f"db connections: using {used_connections} out of {max_connections}"
    elif db_conns_used_percent > max_percent_capacity_consumed * .8:
        return SystemCapacity.CAPPED, f"db connections: using {used_connections} out of {max_connections}"

    return SystemCapacity.AVAILABLE, ""


def get_db_connections_info():
    with connection.cursor() as cursor:
        cursor.execute("SHOW max_connections;")
        max_connections = int(cursor.fetchone()[0])

        cursor.execute("SELECT COUNT(*) FROM pg_stat_activity;")
        used_connections = int(cursor.fetchone()[0])
    return max_connections, used_connections
