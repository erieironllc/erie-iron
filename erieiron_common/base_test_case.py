from django.test import TestCase

from erieiron_common import cache, test_utils
from erieiron_common.aws_utils import set_aws_interface


class BaseTestCase(TestCase):
    @classmethod
    def setUp(cls):
        cache.reset_thread_locals()
        set_aws_interface(test_utils.TestAwsInterface())

    def assert_within_percent(self, v1, v2, max_percent_off, desc=None):
        percent_off = 100 * abs(1 - v1 / v2)
        desc = f"{v1} is not withing {max_percent_off}% of {v2}.  It is {percent_off}% off. {desc}"
        self.assertLess(percent_off, max_percent_off, desc)
