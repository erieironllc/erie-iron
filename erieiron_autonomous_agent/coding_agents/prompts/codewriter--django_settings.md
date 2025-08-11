### DATABASES 
you **must always** configure databases with this code:
```python
rds_secret = erieiron_utils.get_secret_json(os.getenv("RDS_CREDS_ARN"))
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': rds_secret['database'],
        'USER': rds_secret['username'],
        'PASSWORD': rds_secret['password'],
        'HOST': rds_secret['host'],
        'PORT': int(rds_secret['port']),
        'CONN_MAX_AGE': int(os.getenv('DJANGO_DB_CONN_MAX_AGE', '60'))
    }
}

print(f"Database HOST used: {DATABASES['default']['HOST']}")
```
