import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg2
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        conn = psycopg2.connect(dbname="actsights")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT "eventDate", notes
            FROM "ActivityEvents"
            WHERE notes IS NOT NULL
            ORDER BY "eventDate"
        """)

        notes_by_date = defaultdict(list)
        for event_date, notes in cursor.fetchall():
            if not notes or notes.strip() == "":
                continue
            date_key = event_date.strftime("%Y%m%d")
            notes_by_date[date_key].append(notes)

        cursor.close()
        conn.close()

        output_dir = Path.home() / "Downloads" / "exports2"
        output_dir.mkdir(parents=True, exist_ok=True)

        for date_key, notes_list in notes_by_date.items():
            unique_notes = []
            for note in notes_list:
                if note not in unique_notes:
                    unique_notes.append(note)

            file_path = output_dir / f"{date_key}.md"
            file_content = "\n\n".join(unique_notes)
            file_path.write_text(file_content)

        logging.info(f"Exported {len(notes_by_date)} files to {output_dir}")
