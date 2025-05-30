from typing import Tuple

from google.analytics.data_v1beta import BetaAnalyticsDataClient, RunReportRequest, Dimension, Metric, DateRange, FilterExpression, Filter
from google.cloud import bigquery
from google.oauth2 import service_account

import settings
from erieiron_common import common


def get_ga_client():
    credentials = get_gcp_credentials()
    return BetaAnalyticsDataClient(credentials=credentials)


def get_gcp_credentials():
    from erieiron_common import aws_utils
    google_service_key = aws_utils.get_secret("google_service_key-batch")
    credentials = service_account.Credentials.from_service_account_info(google_service_key)
    return credentials


def get_ga_date_ranges(reporting_period):
    return [DateRange(start_date=f"{reporting_period.value}daysAgo", end_date="yesterday")]


def get_biquery_date_filter(reporting_period):
    return f"PARSE_DATE('%Y%m%d', event_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL {reporting_period.value} DAY)"


def get_ga4_rows(
        reporting_period,
        dimensions,
        metrics,
        limit=None,
        order_by_metric=None,
        order_ascending=False,
        row_filter: Tuple = None
):
    dimensions = common.ensure_list(dimensions)
    metrics = common.ensure_list(metrics)

    if order_by_metric is not None and limit is None:
        limit = 10

    if limit is not None:
        order_bys = [{
            "metric": {
                "metric_name": common.default(order_by_metric, metrics[0])
            },
            "desc": (not order_ascending)
        }]
    else:
        order_bys = None

    if row_filter is not None:
        dimension_filter = FilterExpression(
            filter=Filter(
                field_name=row_filter[0],
                string_filter=Filter.StringFilter(
                    value=row_filter[1],
                    match_type=Filter.StringFilter.MatchType.EXACT,  # Exact match for /landing
                ),
            )
        )
    else:
        dimension_filter = None

    request = RunReportRequest(
        property=get_property_path(),
        date_ranges=get_ga_date_ranges(reporting_period),
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        order_bys=order_bys,
        limit=limit,
        dimension_filter=dimension_filter
    )

    return get_ga_client().run_report(request).rows


def get_property_path():
    return f"properties/{settings.GOOGLE_ANALYTICS_PROPERTY_ID}"


def exec_biquery(sql: str):
    query_job = bigquery.Client(credentials=get_gcp_credentials()).query(sql)
    results = query_job.result()
    for row in results:
        yield row
