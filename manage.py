#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

import settings


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

    arg_map = {}
    argv = sys.argv

    for a in argv:
        if not a.startswith("--"):
            continue
        a = a[2:]
        if "=" in a:
            arg_map[a.split("=")[0]] = a.split("=")[1]
        else:
            arg_map[a] = True

    if "erieiron-env" in arg_map:
        os.environ.setdefault('ERIEIRON_ENV_COMMANDLINE', arg_map["erieiron-env"])

    argv = [a for a in argv if "erieiron-env" not in a]

    from erieiron_common.aws_utils import assert_account_name
    assert_account_name(settings.REQUIRED_ACCOUNT_NAME)

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(argv)


if __name__ == '__main__':
    main()
