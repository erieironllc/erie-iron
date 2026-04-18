#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
import warnings
from pathlib import Path


def _extract_erieiron_env(argv: list[str]) -> tuple[str | None, list[str]]:
    filtered_argv = [argv[0]]
    erieiron_env = None
    skip_next_arg = False

    for idx, arg in enumerate(argv[1:], start=1):
        if skip_next_arg:
            skip_next_arg = False
            continue

        if arg == "--erieiron-env":
            if idx + 1 >= len(argv):
                raise SystemExit("--erieiron-env requires a value")
            erieiron_env = argv[idx + 1]
            skip_next_arg = True
            continue

        if arg.startswith("--erieiron-env="):
            erieiron_env = arg.split("=", 1)[1]
            continue

        filtered_argv.append(arg)

    return erieiron_env, filtered_argv


ERIEIRON_ENV_COMMANDLINE, FILTERED_ARGV = _extract_erieiron_env(sys.argv)
if ERIEIRON_ENV_COMMANDLINE:
    os.environ.setdefault("ERIEIRON_ENV_COMMANDLINE", ERIEIRON_ENV_COMMANDLINE)

import settings

tf_plugin_cache = Path(os.path.expanduser("~/.terraform.d/plugin-cache"))
tf_plugin_cache.mkdir(parents=True, exist_ok=True)
os.environ["TF_PLUGIN_CACHE_DIR"] = str(tf_plugin_cache)



def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

    print('connecting to database', settings.DATABASES["default"]["HOST"])

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(FILTERED_ARGV)


if __name__ == '__main__':
    main()
