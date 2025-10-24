## DATABASES 

You **must always** configure databases with this code:
```python
from erieiron_public import agent_tools
DATABASES = agent_tools.get_django_settings_databases_conf()
```

All non-Django components that require database access must instead call `agent_tools.get_database_conf(aws_region_name)`; do not replicate Django's settings logic outside the settings module.
