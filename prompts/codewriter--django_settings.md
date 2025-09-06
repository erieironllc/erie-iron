## DATABASES 

You **must always** configure databases with this code:
```python
from erieiron_public import agent_tools
DATABASES = agent_tools.get_django_settings_databases_conf()
```
