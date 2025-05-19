import os
from functools import wraps

import django
from django.db import connection


def django_required(function):
    @wraps(function)
    def wrap(*args, **kwargs):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
        os.environ.setdefault('ERIEIRON_ENV', 'prod')  # TODO figure out how to get the env from params
        django.setup()

        return function(*args, **kwargs)

    return wrap


def add_col_if_not_exist(schema_editor, table_name, column_name, column_type):
    db_engine = connection.settings_dict['ENGINE']
    if 'postgresql' in db_engine:  # Check if PostgreSQL
        schema_editor.execute(f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT FROM information_schema.columns 
                            WHERE table_schema = 'public' 
                            AND table_name = '{table_name}' 
                            AND column_name = '{column_name}'
                        ) THEN
                            ALTER TABLE public.{table_name} 
                            ADD COLUMN {column_name} {column_type};
                        END IF;
                    END
                    $$;
                """)
    elif 'sqlite' in db_engine:  # Check if SQLite
        cursor = schema_editor.connection.cursor()

        # Check if the column exists
        cursor.execute(f"""
                SELECT EXISTS(
                    SELECT 1 FROM pragma_table_info('{table_name}') WHERE name='{column_name}'
                );
            """)
        exists = cursor.fetchone()[0]

        if not exists:
            cursor.execute(f"""
                    ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type};
                """)
    else:
        raise Exception(f"invalid db engine {db_engine}")
