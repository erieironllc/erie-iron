"""Utilities for collecting CloudFormation failure context and related AWS logs."""
import datetime
import json
import logging
import time
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any

import boto3

from erieiron_common import common
from erieiron_common.aws_utils import get_default_client
from erieiron_common.date_utils import to_utc
from erieiron_common.enums import EnvironmentType

