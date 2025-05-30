import time
from collections import defaultdict

import settings
from erieiron_common import common, cache


class Timer:
    name = None
    mark_names = None
    dict_mark_times = None

    def __init__(self, name):
        self.name = name
        self.mark_names = []
        self.dict_mark_times = defaultdict(list)

    def mark(self, mark_name, printit=False):
        if not settings.SHOW_TIMERS:
            return
        if mark_name not in self.mark_names:
            self.mark_names.append(mark_name)

        mark_times = self.dict_mark_times[mark_name]
        mark_times.append(time.time())

        if printit:
            if len(mark_times) > 1:
                common.log_info(self.name, mark_name, mark_times[-1] - mark_times[0])
            else:
                common.log_info(self.name, mark_name, 0)

    def print_total_time(self):
        common.log_info("timer total time %s\t%s" % (self.name, self.get_total_time()))

    def get_total_time(self):
        if not settings.SHOW_TIMERS:
            return 0

        earliest_time = None
        latest_time = None
        for mark_name in self.mark_names:
            t = self.dict_mark_times[mark_name]
            if earliest_time is None:
                earliest_time = t
            if latest_time is None:
                latest_time = t
            earliest_time = min(earliest_time, t)
            latest_time = min(latest_time, t)

        if earliest_time is None or latest_time is None:
            return 0
        else:
            return float(latest_time) - float(earliest_time)

    def dump(self, sort_by_longest=False, floor_time=None):
        if not settings.SHOW_TIMERS:
            return

        if len(self.dict_mark_times) == 0:
            common.log_info("timer %s is empty" % self.name)
            return

        count_passes = 0
        for mark_name in self.dict_mark_times:
            count_passes = max(len(self.dict_mark_times[mark_name]), count_passes)

        total_times = defaultdict(list)
        all_times = []
        for passidx in range(count_passes):
            start_time = None
            for mark_name in self.mark_names:
                mark_times = self.dict_mark_times[mark_name]
                if passidx < len(mark_times):
                    mark_time = mark_times[passidx]
                    all_times.append(mark_time)
                    if start_time is not None:
                        total_times[mark_name].append(mark_time - start_time)
                    start_time = mark_time

        marks = self.mark_names
        if sort_by_longest:
            marks = list(sorted(marks, key=lambda m1: sum(total_times[m1]), reverse=True))
        for m in marks:
            total_time = sum(total_times[m])
            if floor_time is not None and total_time < floor_time:
                continue

            common.log_info("timer\t%s" % "\t".join([
                self.name,
                str(m),
                str(total_time),
                str(len(total_times[m])),
                str(common.safe_divide(sum(total_times[m]), len(total_times[m])))
            ]))

        common.log_info("total\t%s" % "\t".join([self.name, "\t", str(max(all_times) - min(all_times))]))


def reset():
    cache.tl_set("timers", {})


def dump(timer_name=None, sort_by_longest=True, floor_time=None):
    if not settings.SHOW_TIMERS:
        return

    timers = cache.tl_get("timers", {})
    if len(timers) == 0:
        common.log_info("no timers")
    else:
        if timer_name is None:
            for timer_name in timers:
                timers[timer_name].dump(sort_by_longest, floor_time=floor_time)
        else:
            timers[timer_name].dump(sort_by_longest, floor_time=floor_time)


def mark(timer_name, mark_name, printit=False):
    if not settings.SHOW_TIMERS:
        return

    timers = cache.tl_get("timers", {})
    timer = common.get(timers, timer_name)
    if timer is None:
        timer = Timer(timer_name)
        timers[timer_name] = timer
        cache.tl_set("timers", timers)
    timer.mark(mark_name, printit)
