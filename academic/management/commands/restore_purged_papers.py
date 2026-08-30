"""Restore purged papers into the pending review queue, not back into public view.

The 2026-08-30 purges (pre-1925 unlinked, and unverifiable-authorship after
directory cross-reference) deleted 5,982 papers outright. Per explicit
instruction, they are restored here instead - but as `review_status='pending'`,
invisible to every public-facing endpoint, so an admin can work through them one
by one with the same approve/reject pattern already used for Faculty, rather
than the purge being silently undone.

Reads the full field set directly from a source sqlite3 file (read-only, no
Django ORM against a second connection) and inserts any paper missing from the
current DB by DOI - the stable key across databases whose autoincrement PKs
differ. Never touches a paper that still exists (nothing here can overwrite a
kept, verified record). Dry-run by default; --apply writes.
"""

import json
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from academic.models import Paper

# Every concrete column on Paper except id/doi (the join key) and the two new
# review fields, which are always set fresh here, not copied from the backup.
_RESTORE_FIELDS = [
    "title", "abstract", "journal", "date_published", "download_url", "license_url",
    "ai_keywords", "faculty_keywords", "tc_count", "date_published_online",
    "date_published_print", "url", "keywords", "themes", "top_level_categories",
    "mid_level_categories", "low_level_categories", "category_urls",
    "top_category_urls", "mid_category_urls", "low_category_urls", "faculty_members",
    "faculty_affiliations", "source_metadata", "engagement_metrics", "source_record",
    "paper_embedding", "embedding_model", "embedding_updated_at",
]
_JSON_FIELDS = {
    "ai_keywords", "faculty_keywords", "keywords", "themes", "top_level_categories",
    "mid_level_categories", "low_level_categories", "category_urls", "top_category_urls",
    "mid_category_urls", "low_category_urls", "faculty_members", "faculty_affiliations",
    "source_metadata", "engagement_metrics", "source_record", "paper_embedding",
}


class Command(BaseCommand):
    help = "Restore purged papers as review_status='pending' from a pre-purge backup"

    def add_arguments(self, parser):
        parser.add_argument("--source-db", required=True, help="Path to the pre-purge db.sqlite3")
        parser.add_argument("--note", default="", help="review_note to stamp on restored rows")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        source_path = Path(opts["source_db"])
        if not source_path.exists():
            raise CommandError(f"No such file: {source_path}")

        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        cols = ", ".join(["doi"] + _RESTORE_FIELDS)
        source_rows = {row["doi"]: row for row in src.execute(f"SELECT {cols} FROM academic_paper")}
        src.close()

        current_dois = set(Paper.objects.values_list("doi", flat=True))
        missing = [doi for doi in source_rows if doi and doi not in current_dois]

        self.stdout.write(f"source rows            : {len(source_rows)}")
        self.stdout.write(f"already present         : {len(source_rows) - len(missing)}")
        self.stdout.write(f"to restore as pending   : {len(missing)}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to write."))
            return

        created = 0
        for doi in missing:
            row = source_rows[doi]
            fields = {}
            for name in _RESTORE_FIELDS:
                value = row[name]
                if name in _JSON_FIELDS and value is not None:
                    value = json.loads(value) if isinstance(value, str) else value
                fields[name] = value
            Paper.objects.create(
                doi=doi,
                review_status="pending",
                review_note=opts["note"],
                **fields,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"restored {created} papers as pending"))
