import calendar
from datetime import timedelta, datetime, date

from dateutil import parser
from django.utils.timezone import localtime

from erieiron_common.common import is_empty, safe_max, filter_none, safe_min, is_numeric, safe_divide, HashableDict

DATE_FORMAT = '%m/%d/%Y'
DATE_KEY_FORMAT = '%Y.%m.%d'
MONTH_KEY_FORMAT = '%Y.%m'
DATE_TIME_FORMAT = FILE_NAME_TIME_FORMAT = '%Y%m%d_%H%M%S'
PSQL_DATE_FORMAT = '%Y-%m-%d'
DATEINPUT_DATE_FORMAT = '%Y-%m-%d'

DATE_FORMATS = [
    DATE_FORMAT,
    DATE_KEY_FORMAT,
    MONTH_KEY_FORMAT,
    DATE_TIME_FORMAT,
    FILE_NAME_TIME_FORMAT,
    PSQL_DATE_FORMAT,
    DATEINPUT_DATE_FORMAT,
    '%m/%d/%y'
]

MONTH_NAMES_SHORT = ["Jan",
                     "Feb",
                     "Mar",
                     "Apr",
                     "May",
                     "Jun",
                     "Jul",
                     "Aug",
                     "Sep",
                     "Oct",
                     "Nov",
                     "Dec"
                     ]

MONTH_NAMES = ["January",
               "February",
               "March",
               "April",
               "May",
               "June",
               "July",
               "August",
               "September",
               "October",
               "November",
               "December"
               ]


def ensure_date(d, none_to_today=False):
    if d is None:
        if none_to_today:
            return datetime.today().date()
        else:
            return None
    elif isinstance(d, datetime):
        return d.date()
    elif isinstance(d, date):
        return d
    elif isinstance(d, str):
        return parse_date(d)

    raise Exception("%s is not a date" % str(d))


def ensure_datetime(dt, none_to_now=False):
    if dt is None:
        if none_to_now:
            return localtime(datetime.now())
        else:
            return None
    elif isinstance(dt, datetime):
        return localtime(dt)
    elif isinstance(dt, date):
        # Convert date to datetime at midnight
        return localtime(datetime.combine(dt, datetime.min.time()))
    elif isinstance(dt, str):
        # Parse the string to a datetime
        return parse_datetime(dt)

    raise Exception(f"{dt} is not a datetime")


def is_date(d):
    return isinstance(d, datetime)


def get_month_key(daytime_date):
    if daytime_date is None:
        return None
    else:
        return ensure_date(daytime_date).strftime(MONTH_KEY_FORMAT)


def get_monthkey_range(start_date, end_date=None, reverse=False):
    if is_empty(start_date):
        start_date = datetime.today()

    if is_empty(end_date):
        end_date = datetime.today()

    if not is_date(start_date):
        start_date = parse_date(start_date)

    dates = []
    month_key = get_month_key(start_date)
    while month_key <= get_month_key(end_date):
        dates.append(month_key)
        month_key = increment_month_key(month_key)

    if reverse:
        return list(reversed(dates))
    else:
        return dates


def get_datekey_range(start_date, end_date=None, reverse=False):
    if is_empty(start_date):
        start_date = datetime.today()

    if is_empty(end_date):
        end_date = datetime.today()

    if not is_date(start_date):
        start_date = parse_date(start_date)

    dates = []
    date_key = get_date_key(start_date)
    while date_key <= get_date_key(end_date):
        dates.append(date_key)
        date_key = increment_date_key(date_key)

    if reverse:
        return list(reversed(dates))
    else:
        return dates


def months_to_quarters(months):
    quarters = set()
    for month in months:
        quarters.add(get_quarter_key(month))
    return list(sorted(quarters))


def get_quarter_key(daytime_date):
    if daytime_date is None:
        return None
    else:
        if isinstance(daytime_date, str):
            daytime_date = parse_date(daytime_date)
        return "%sQ%s" % (daytime_date.year, int((daytime_date.month - 1) / 3) + 1)


def get_year_key(daytime_date):
    if daytime_date is None:
        return None
    else:
        return str(daytime_date.year)


def get_yearhalf_key(daytime_date):
    if daytime_date is None:
        return None
    else:
        if isinstance(daytime_date, str):
            daytime_date = parse_date(daytime_date)
        if daytime_date.month <= 6:
            part = "1"
        else:
            part = "2"
        return "%sH%s" % (daytime_date.year, part)


def format_with_time(d):
    if d is None:
        return ""

    return ensure_datetime(d).strftime("%m/%d/%Y %H:%M:%S")


def format_month_friendly(month_key):
    if month_key is None:
        return None

    month_key_parts = month_key.split(".")

    return "%s %s" % (MONTH_NAMES_SHORT[int(month_key_parts[1]) - 1], month_key_parts[0])


def format_month_key(month_key, none_val=None):
    if is_empty(month_key):
        return none_val

    month_key_parts = month_key.split(".")

    return "%s 1st, %s" % (MONTH_NAMES[int(month_key_parts[1]) - 1], month_key_parts[0])


def parse_quarter_key(quarter_key, lastday=False):
    if quarter_key is None or 'Q' not in quarter_key:
        return None

    if lastday:
        return parse_quarter_key(increment_quarter_key(quarter_key)) - timedelta(days=1)
    else:
        parts = quarter_key.split("Q")
        year = int(parts[0])
        quarter = int(parts[1])
        month = 1 + (quarter - 1) * 3
        return parse_date("%s/1/%s" % (month, year))


def get_first_day_this_month():
    return parse_date_key(get_date_key_first_of_month(datetime.today()))


def is_month_key(month_key):
    return len(month_key.split(".")) == 2


def parse_yearhalf_key(yearhalf_key, lastday=False):
    if lastday:
        return parse_yearhalf_key(increment_yearhalf_key(yearhalf_key)) - timedelta(days=1)
    else:
        year = yearhalf_key.split("H")[0]
        half = int(yearhalf_key.split("H")[1])
        if half == 1:
            month = ".01"
        else:
            month = ".07"
        return parse_month_key(year + month)


def parse_year_key(year_key, lastday=False):
    if lastday:
        return parse_year_key(increment_year_key(year_key)) - timedelta(days=1)
    else:
        return parse_date("%s.01.01" % year_key)


def parse_month_key(month_key, lastday=False):
    if month_key is None:
        return None

    if lastday:
        return parse_month_key(increment_month_key(month_key)) - timedelta(days=1)
    else:
        return parse_date(month_key + ".01")


def get_date_key_type(date_key):
    if is_empty(date_key):
        raise Exception("can't get type of empty date key")

    date_key = date_key.upper()

    if len(date_key) == 4 and is_numeric(date_key):
        # 2019
        return "year"
    elif len(date_key) == 6 and "H" in date_key:
        # 2019H1
        return "yearhalf"
    elif len(date_key) == 6 and "Q" in date_key:
        # 2019Q1
        return "quarter"
    elif len(date_key) == 7:
        # 2019.01
        return "month"
    else:
        # 2019.01.01
        return "day"


def increment_timeperiod_key(date_key, inc=1):
    date_key_type = get_date_key_type(date_key)
    date_key = date_key.upper()

    if date_key_type == "year":
        return increment_year_key(date_key, inc=inc)
    elif date_key_type == "yearhalf":
        return increment_yearhalf_key(date_key, inc=inc)
    elif date_key_type == "quarter":
        return increment_quarter_key(date_key, inc=inc)
    elif date_key_type == "month":
        return increment_month_key(date_key, inc=inc)
    elif date_key_type == "day":
        return increment_date_key(date_key, inc=inc)
    elif date_key_type == "rolling30":
        return None
    else:
        raise Exception("invalid date key type %s: %s" % (date_key_type, date_key))


def parse_date_key(date_key, lastday=False):
    if isinstance(date_key, datetime):
        return date_key.date()
    elif isinstance(date_key, date):
        return date_key

    date_key_type = get_date_key_type(date_key)
    date_key = date_key.upper()

    if date_key_type == "year":
        return parse_year_key(date_key, lastday=lastday)
    elif date_key_type == "yearhalf":
        return parse_yearhalf_key(date_key, lastday=lastday)
    elif date_key_type == "quarter":
        return parse_quarter_key(date_key, lastday=lastday)
    elif date_key_type == "month":
        return parse_month_key(date_key, lastday=lastday)
    elif date_key_type == "day":
        return datetime.strptime(date_key, "%Y.%m.%d").date()
    elif date_key_type == "rolling30":
        return ensure_date(datetime.today() - timedelta(days=30))
    else:
        raise Exception("invalid date key type %s: %s" % (date_key_type, date_key))


def get_date_key_of_type(d, date_key_type):
    if date_key_type == "year":
        return get_year_key(d)
    elif date_key_type == "yearhalf":
        return get_yearhalf_key(d)
    elif date_key_type == "quarter":
        return get_quarter_key(d)
    elif date_key_type == "month":
        return get_month_key(d)
    elif date_key_type == "day":
        return get_date_key(d)
    else:
        raise Exception(f"invalid date key type {date_key_type}: {d}")


def parse_date_key_with_meta(date_key, include_prev_next=True, only_serializeable=False):
    from . import cache
    cache_key = date_key + ("include_prev_next" if include_prev_next else "") + (
        "only_serializeable" if only_serializeable else "")
    cached_val = cache.tl_get(cache_key)
    if cached_val is not None:
        return cached_val

    t = get_date_key_type(date_key)
    today = datetime.today().date()
    date_key_today = get_date_key(today)

    first_date, last_date = get_first_last_date(date_key)
    days = get_days_btw(first_date, last_date)
    days_completed = get_days_btw(first_date, safe_min(ensure_dates([last_date, today])))

    day_keys = [get_date_key(d) for d in days]

    percent_complete = safe_divide(len(days_completed), len(days), 0)
    month_keys = get_monthkeys_between(first_date, last_date)
    monthkey_today = get_month_key(datetime.today())
    month_keys_remaining = [mk for mk in month_keys if mk >= monthkey_today]
    d = {
        "original_date_key": date_key,
        "type": t,
        "date_key": get_date_key(first_date),
        "first_date_key": get_date_key(first_date),
        "last_date_key": get_date_key(last_date),
        "contains_past": safe_date1_greater(today, first_date, gte=True),
        "contains_today": date_key_today in day_keys,
        "contains_future": safe_date1_greater(last_date, today),
        "percent_complete": percent_complete,
        "percent_remaining": 1 - percent_complete,
        "day_keys": day_keys,
        "weekday_keys": [get_date_key(d) for d in days if d.weekday() < 5],
        "month_keys": month_keys,
        "month_keys_remaining": month_keys_remaining,
    }
    if not only_serializeable:
        d["first_date"] = d["date"] = first_date
        d["last_date"] = last_date
        d["days"] = days

    if include_prev_next:
        prev_date_key = increment_timeperiod_key(date_key, inc=-1)
        if prev_date_key is not None:
            d["previous_period"] = parse_date_key_with_meta(prev_date_key, include_prev_next=False)

        next_date_key = increment_timeperiod_key(date_key, inc=1)
        if next_date_key is not None:
            d["next_period"] = parse_date_key_with_meta(next_date_key, include_prev_next=False)

    cache.tl_set(cache_key, d)

    return d


def get_date_key_first_of_month(daytime_date):
    if daytime_date is None:
        return None

    date_key = get_date_key(get_prev_monday(daytime_date))
    for i in range(100):
        daytime_date -= timedelta(days=7)
        date_key2 = get_date_key(get_prev_monday(daytime_date))
        if date_key[:8] != date_key2[:8]:
            return date_key

    raise Exception("should never get here")


def get_count_days_in_current_month():
    now = datetime.now()
    return calendar.monthrange(now.year, now.month)[1]


def get_date_key(daytime_date):
    if daytime_date is None:
        return None
    else:
        return ensure_date(daytime_date).strftime(DATE_KEY_FORMAT)


def increment_date_key(day_key, inc=1):
    return get_date_key(parse_date_key(day_key) + timedelta(days=inc))


def increment_year_key(year_key, inc=1):
    if year_key is None:
        return None

    year_part = int(year_key)
    year_part += inc
    return str(year_part)


def increment_yearhalf_key(yearhalf_key, inc=1):
    if yearhalf_key is None:
        return None

    parts = yearhalf_key.split("H")
    year_part = int(parts[0])
    h_part = int(parts[1])

    h_part += inc

    if h_part > 2:
        h_part = 1
        year_part += 1
    elif h_part == 0:
        h_part = 2
        year_part -= 1

    return "%sH%s" % (year_part, h_part)


def increment_quarter_key(quarter_key, inc=1):
    if quarter_key is None:
        return None

    parts = quarter_key.split("Q")
    year_part = int(parts[0])
    q_part = int(parts[1])

    q_part += inc

    if q_part > 4:
        q_part = 1
        year_part += 1
    elif q_part == 0:
        q_part = 4
        year_part -= 1

    return "%sQ%s" % (year_part, q_part)


def is_between(test_date, first_date, last_date):
    if test_date is None:
        return False

    test_date = get_date_key(test_date)
    first_date = get_date_key(first_date)
    last_date = get_date_key(last_date)

    return first_date <= test_date <= last_date


def date_in_range(test_date, timeperiod_key):
    if test_date is None:
        return False

    first_date, last_date = get_first_last_date(timeperiod_key)
    return is_between(test_date, first_date, last_date)


def get_first_last_date(timeperiod_key):
    if is_empty(timeperiod_key):
        return None, None

    if str(timeperiod_key).lower() == "current":
        return datetime.today().date() - timedelta(days=30), datetime.today().date()
    elif isinstance(timeperiod_key, datetime):
        return timeperiod_key.date(), timeperiod_key.date()
    elif isinstance(timeperiod_key, date):
        return timeperiod_key, timeperiod_key
    else:
        dtype = get_date_key_type(timeperiod_key)
        if dtype == "year":
            return parse_year_key(timeperiod_key), parse_year_key(timeperiod_key, lastday=True)
        elif dtype == "yearhalf":
            return parse_yearhalf_key(timeperiod_key), parse_yearhalf_key(timeperiod_key, lastday=True)
        elif dtype == "quarter":
            return parse_quarter_key(timeperiod_key), parse_quarter_key(timeperiod_key, lastday=True)
        elif dtype == "month":
            return parse_month_key(timeperiod_key), parse_month_key(timeperiod_key, lastday=True)
        elif dtype == "day":
            d = parse_date_key(timeperiod_key)
            return d, d
        elif dtype == "rolling30":
            dt_today = datetime.today()
            return ensure_date(dt_today - timedelta(days=30)), dt_today


def last_datekey_of_month(date_key):
    if date_key is None:
        return None

    return get_date_key(parse_date_key(increment_month_key(date_key)) - timedelta(days=1))


def increment_month_key(month_key, inc=1):
    if month_key is None:
        return None

    for idx in range(inc):
        month_key = _increment_month_key(month_key)

    return month_key


def _increment_month_key(month_key):
    month_key = "".join(month_key.split("."))
    year_part = month_key[0:4]
    month_part = month_key[4:]
    month_part = int(month_part) + 1
    if month_part < 10:
        month_part = "0" + str(month_part)
    elif month_part > 12:
        year_part = str(int(year_part) + 1)
        month_part = "01"
    else:
        month_part = str(month_part)
    return ".".join([year_part, month_part])


def increment_week_key(wk, inc=1):
    return get_week_key(parse_date(wk) + timedelta(days=(7 * inc)))


def get_week_key_label_values(current_wk, d, d_last=None):
    if current_wk is None:
        current_wk = get_week_key(datetime.today())

    week_key_label_values = []
    for wk in get_week_keys(d, d_last):
        week_key_label_values.append({
            "key": wk,
            "label": parse_date(wk).strftime(DATE_FORMAT),
            "selected": wk == current_wk,

        })
    return week_key_label_values


def get_week_keys(d, d_last=None):
    weeks_keys = []
    wk = get_week_key(ensure_date(d, none_to_today=True))
    wk_last = get_week_key(ensure_date(d_last))

    while wk <= wk_last:
        weeks_keys.append(wk)
        wk = increment_week_key(wk)

    return weeks_keys


def get_week_key(d=None):
    if d is None:
        d = datetime.today()

    if d.weekday() > 0:
        d = get_prev_monday(d)

    return get_date_key(d - timedelta(days=d.weekday()))


def get_month_data(m):
    if isinstance(m, str):
        d = get_month_key(parse_date(m))
    else:
        d = get_month_key(m)

    year = int(d.split(".")[0])
    month = d.split(".")[1]
    month_index = int(month) - 1

    return HashableDict({
        "key": d,
        "year": year,
        "month": d.split(".")[1],
        "month_long": MONTH_NAMES[month_index],
        "title_short": MONTH_NAMES_SHORT[month_index],
        "title_long": MONTH_NAMES[month_index] + " " + str(year)
    })


def parse_date_or_none(date_string):
    try:
        return parse_date(date_string)
    except:
        return None


def parse_date(date_string):
    return ensure_date(
        parse_datetime(date_string)
    )


def parse_datetime(date_string):
    try:
        return localtime(parser.parse(date_string))
    except Exception as e:
        print(f"Error parsing datetime: {e}")
        return None


def get_monthkeys_between(start_date, end_date):
    mks = []

    mk_start = get_month_key(parse_date_key(start_date))
    mk_end = get_month_key(parse_date_key(end_date, lastday=True))

    mk = mk_start
    while mk <= mk_end:
        mks.append(mk)
        mk = increment_month_key(mk)

    return mks


def get_days_btw(start_date, end_date, weekdays_only=False):
    days = []

    start_date = parse_date_key(start_date)
    end_date = parse_date_key(end_date, lastday=True)

    d = ensure_date(start_date)
    while d <= end_date:
        days.append(d)
        d += timedelta(days=1)

    if weekdays_only:
        return [d for d in days if d.weekday() < 5]
    else:
        return days


def get_common_days(avail_days, first_date, last_date):
    if last_date is None:
        return list(filter(lambda ad: ensure_date(ad) >= first_date, avail_days))
    else:
        return list(filter(lambda ad: first_date <= ensure_date(ad) <= last_date, avail_days))


def remove_days(avail_days, first_date, last_date):
    return list(filter(lambda ad: not (first_date <= ensure_date(ad) <= last_date), avail_days))


def ensure_dates(dates):
    return [ensure_date(d) for d in dates]


def safe_date1_greater(date1, date2, gte=False):
    if date2 is None or date1 is None:
        return False

    date1 = get_date_key(ensure_date(date1))
    date2 = get_date_key(ensure_date(date2))

    if gte:
        return date1 >= date2
    else:
        return date1 > date2


def is_after_today(d):
    return safe_date1_greater(d, datetime.today())


def is_before_today(d):
    return safe_date1_greater(datetime.today(), d)


def safe_max_date(vals):
    return safe_max(ensure_dates(filter_none(vals)))


def safe_min_date(vals):
    return safe_min(ensure_dates(filter_none(vals)))


def get_percent_d2_intersecting(start_date_1, end_date_1, date_key_2):
    days_1 = [get_date_key(d) for d in get_days_btw(start_date_1, end_date_1)]
    days_2 = [get_date_key(d) for d in parse_date_key_with_meta(date_key_2, include_prev_next=False)['days']]

    common_days = len([d for d in days_1 if d in days_2])
    return safe_divide(common_days, len(days_2))


def percent_month_left(month_key, date_for_left=None):
    return get_month_meta(month_key, date_for_left)['percent_month_left']


def get_month_meta(month_key, date_for_left=None):
    if date_for_left is None:
        date_for_left = datetime.today()
    date_for_left = ensure_date(date_for_left)

    month_key_now = get_month_key(datetime.today())
    this_month_start_date = parse_month_key(month_key)
    next_month_start_date = parse_date_key(increment_month_key(month_key) + ".01")
    earliest_day_in_month = this_month_start_date
    latest_day_in_month = next_month_start_date - timedelta(days=1)
    total_days_in_month = (latest_day_in_month - this_month_start_date).days
    days_left_in_month = total_days_in_month - (date_for_left - this_month_start_date).days
    percent_month_remaining = safe_divide(days_left_in_month, total_days_in_month)

    return {
        "is_current_month": month_key == month_key_now,
        "month_key": month_key,
        "month_key_now": month_key_now,
        "earliest_day_in_month": earliest_day_in_month,
        "latest_day_in_month": latest_day_in_month,
        "this_month_start_date": this_month_start_date,
        "next_month_start_date": next_month_start_date,
        "total_days_in_month": total_days_in_month,
        "percent_month_left": percent_month_remaining
    }


def get_date_key_prev_monday(date_key):
    date_key_first_day = get_date_key(
        get_prev_monday(
            parse_date_key(
                date_key
            )
        )
    )
    return date_key_first_day


def get_prev_monday(d=None):
    if d is None:
        d = datetime.today()

    offset = (d.weekday()) % 7
    return d - timedelta(days=offset)
