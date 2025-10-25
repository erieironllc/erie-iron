## DATABASES 

You **must always** configure databases with this code:
```python
from erieiron_public import agent_tools
DATABASES = agent_tools.get_django_settings_databases_conf()
```

All non-Django components that require database access must instead call `get_pg8000_connection()` from `erieiron_public.agent_tools` and reuse the shared snippet:
```python
with get_pg8000_connection() as conn:
    conn.cursor().execute(<sql>)
```
Do not replicate Django's settings logic outside the settings module.
