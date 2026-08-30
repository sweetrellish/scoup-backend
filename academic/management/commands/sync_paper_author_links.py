"""Copy Paper<->Faculty author links from a source DB, matched by faculty_id and DOI.

`sync_directory_verified_faculty` (2026-08-29) copied Faculty scalar fields from
the repo DB to live but never touched the `academic_paper_authors` M2M table, so
the 87 dual-source faculty it created on live looked correct (article_count, etc.)
but had zero real linked papers. The 2026-08-30 03:42 nightly worker caught this
correctly - `recalc_faculty_metrics` zeroed their article_count to match the
actually-empty M2M, which is the right behavior for that command, but it means
the true links still need restoring here, not just the display number.

Faculty PKs differ between repo and live (independently auto-incremented), so
this matches by the stable natural key `faculty_id`, and links papers by DOI
(also stable across both databases) rather than by Paper PK.

Dry-run by default; --apply writes.
"""

import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from academic.models import Faculty, Paper


class Command(BaseCommand):
    help = "Copy Paper<->Faculty author links from a source sqlite3 file, matched by faculty_id/doi"

    def add_arguments(self, parser):
        parser.add_argument("--source-db", required=True, help="Path to the source db.sqlite3")
        parser.add_argument("--apply", action="store_true", help="Write changes")

    def handle(self, *args, **opts):
        source_path = Path(opts["source_db"])
        if not source_path.exists():
            raise CommandError(f"No such file: {source_path}")

        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row

        rows = src.execute(
            """
            SELECT f.faculty_id AS faculty_id, p.doi AS doi
            FROM academic_paper_authors pa
            JOIN academic_faculty f ON f.id = pa.faculty_id
            JOIN academic_paper p ON p.id = pa.paper_id
            WHERE p.doi != ''
            """
        ).fetchall()
        src.close()

        by_faculty = {}
        for row in rows:
            by_faculty.setdefault(row["faculty_id"], []).append(row["doi"])

        self.stdout.write(f"source faculty with linked papers : {len(by_faculty)}")
        self.stdout.write(f"source paper-author links total   : {len(rows)}")

        checked_faculty = 0
        added_links = 0
        missing_faculty = 0
        missing_papers = 0

        for faculty_id, dois in by_faculty.items():
            faculty = Faculty.objects.filter(faculty_id=faculty_id).first()
            if faculty is None:
                missing_faculty += 1
                continue
            checked_faculty += 1

            existing_dois = set(faculty.papers.values_list("doi", flat=True))
            missing = [d for d in dois if d not in existing_dois]
            if not missing:
                continue

            papers = list(Paper.objects.filter(doi__in=missing))
            found_dois = {p.doi for p in papers}
            missing_papers += len(missing) - len(found_dois)

            if papers and opts["apply"]:
                for paper in papers:
                    paper.authors.add(faculty)
            added_links += len(papers)

        self.stdout.write(f"faculty matched by faculty_id      : {checked_faculty}")
        self.stdout.write(f"faculty in source but not here     : {missing_faculty}")
        self.stdout.write(f"links added                        : {added_links}")
        self.stdout.write(f"papers referenced but not found here: {missing_papers}")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to write."))
        else:
            self.stdout.write(self.style.WARNING(
                "Now run recalc_faculty_metrics --apply to bring article_count/citations back in sync."
            ))
