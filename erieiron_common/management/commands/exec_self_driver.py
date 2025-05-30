from django.core.management.base import BaseCommand

from erieiron_common import common
from erieiron_coder.self_driving_code import self_driver


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument(
            '--eval',
            type=bool,
            required=False
        )

        parser.add_argument(
            '--config',
            type=str,
            required=False
        )

        parser.add_argument(
            '--code_file',
            type=str,
            required=False
        )

    def handle(self, *args, **options):
        if options.get("eval"):
            self_driver.execute_eval(
                options.get("config")
            )
        elif options.get("code_file"):
            python_file = options.get("code_file")
            print(f"executing {python_file}")
            code_module = common.import_module_from_path(python_file)
            code_module.execute()
        elif options.get("config"):
            self_driver.execute(
                options.get("config")
            )
        else:
            raise Exception(f"must supply either a code_file or a self_driver module")
