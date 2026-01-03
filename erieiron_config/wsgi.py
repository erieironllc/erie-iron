"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os
import warnings
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

tf_plugin_cache = Path(os.path.expanduser("~/.terraform.d/plugin-cache"))
tf_plugin_cache.mkdir(parents=True, exist_ok=True)
os.environ["TF_PLUGIN_CACHE_DIR"] = str(tf_plugin_cache)


application = get_wsgi_application()
