"""One-off sync: copy directory_verified Faculty rows from the repo DB into whatever
DB this command is run against (intended: live).

Context: the 87 dual-source faculty created on 2026-08-29 (cross-referencing OpenAlex
authorships against the SU directory) were only ever applied to the repo DB - noted
as a gap at the time, never closed. Live plateaued at 95 directory_verified faculty
while the repo reached 182, which is what made the live "faculty count" look wrong
today. This is not data loss; it is a deploy step that was never done.

Reads the *other* sqlite3 file directly (read-only, stdlib sqlite3 - no Django ORM,
so it never touches this process's configured database) and upserts by the unique
`faculty_id`: creates missing rows, fills only blank fields on existing rows unless
--overwrite is passed. Dry-run by default.
"""

import sqlite3

from django.core.management.base import BaseCommand, CommandError

from academic.models import Faculty

FACULTY_FIELDS = [
    "faculty_id", "orcid", "openalex_id", "first_name", "last_name", "name",
    "title", "department", "school", "email", "office", "room", "phone", "bio",
    "profile_visibility", "directory_verified", "is_approved", "review_status",
    "review_note", "institutional_email", "institutional_email_verified",
    "total_citations", "article_count", "average_citations",
    "department_affiliations", "dois", "titles", "categories", "keywords",
    "top_level_categories", "mid_level_categories", "low_level_categories",
    "category_urls", "top_category_urls", "mid_category_urls", "low_category_urls",
    "themes", "journals",
]


class Command(BaseCommand):
    help = "Sync directory_verified Faculty rows from a source sqlite3 file (e.g. the repo DB)"

    def add_arguments(self, parser):
        parser.add_argument("--source-db", required=True, help="Path to the source db.sqlite3")
        parser.add_argument("--apply", action="store_true", help="Write changes")
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite non-blank fields on existing rows (default: fill blanks only)",
        )

    def handle(self, *args, **opts):
        try:
            src = sqlite3.connect(f"file:{opts['source_db']}?mode=ro", uri=True)
        except sqlite3.OperationalError as exc:
            raise CommandError(f"Cannot open source DB: {exc}")
        src.row_factory = sqlite3.Row

        cols = ", ".join(FACULTY_FIELDS)
        rows = src.execute(
            f"SELECT {cols} FROM academic_faculty WHERE directory_verified = 1"
        ).fetchall()
        src.close()

        created, updated, unchanged, skipped_blank_id = 0, 0, 0, 0

        for row in rows:
            data = dict(row)
            fid = data.get("faculty_id")
            if not fid:
                skipped_blank_id += 1
                continue

            existing = Faculty.objects.filter(faculty_id=fid).first()
            if existing is None:
                if opts["apply"]:
                    Faculty.objects.create(**data)
                created += 1
                continue

            changed = False
            for field, value in data.items():
                if field == "faculty_id":
                    continue
                current = getattr(existing, field)
                is_blank = current in (None, "", [], {}, 0, 0.0, False)
                if opts["overwrite"] or is_blank:
                    if current != value:
                        if opts["apply"]:
                            setattr(existing, field, value)
                        changed = True

            if changed:
                if opts["apply"]:
                    existing.save()
                updated += 1
            else:
                unchanged += 1

        self.stdout.write(f"source directory_verified rows : {len(rows)}")
        self.stdout.write(f"created                         : {created}")
        self.stdout.write(f"updated                         : {updated}")
        self.stdout.write(f"unchanged                       : {unchanged}")
        self.stdout.write(f"skipped (blank faculty_id)      : {skipped_blank_id}")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to write."))
