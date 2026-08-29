"""Match Faculty records against the SU directory PDF and enrich display fields.

Dry-run by default. Use --apply to write.
"""

import collections
import re
from datetime import timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from academic.directory_parser import parse_directory
from academic.models import Faculty

# Directory rows for non-academic units are staff, not research faculty.
ACADEMIC_TITLE_HINTS = (
    "professor",
    "lecturer",
    "instructor",
    "department chair",
    "dean",
    "faculty",
    "researcher",
    "scientist",
)


def normalize(value):
    return re.sub(r"[^a-z]", "", (value or "").lower())


def looks_academic(title):
    lowered = (title or "").lower()
    return any(hint in lowered for hint in ACADEMIC_TITLE_HINTS)


class Command(BaseCommand):
    help = "Enrich Faculty rows (title, department, room, phone) from SUdirectory.pdf"

    def add_arguments(self, parser):
        parser.add_argument("--pdf", default="SUdirectory.pdf")
        parser.add_argument("--apply", action="store_true", help="Write changes")
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace existing values instead of only filling blanks",
        )
        parser.add_argument(
            "--academic-only",
            action="store_true",
            help="Only match directory rows with an academic title",
        )
        parser.add_argument(
            "--include-initial",
            action="store_true",
            help=(
                "Also write first-initial-only matches. Off by default: these produced "
                "false positives (e.g. an external co-author labelled Physics faculty)."
            ),
        )
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **opts):
        pdf_path = opts["pdf"]
        apply_changes = opts["apply"]
        overwrite = opts["overwrite"]

        try:
            rows = parse_directory(pdf_path)
        except FileNotFoundError:
            raise CommandError(f"Directory PDF not found: {pdf_path}")

        if opts["academic_only"]:
            rows = [r for r in rows if looks_academic(r["title"])]

        by_last = collections.defaultdict(list)
        for row in rows:
            by_last[normalize(row["last_name"])].append(row)

        self.stdout.write(f"directory rows: {len(rows)}")

        stats = collections.Counter()
        updates = []
        review_candidates = []

        for faculty in Faculty.objects.all():
            last = normalize(faculty.last_name)
            first = normalize(faculty.first_name)
            if not last and faculty.name:
                parts = faculty.name.split()
                last = normalize(parts[-1])
                first = normalize(parts[0]) if len(parts) > 1 else ""

            candidates = by_last.get(last, [])
            if not candidates:
                stats["unmatched"] += 1
                continue

            match = None
            confidence = ""

            exact = [c for c in candidates if normalize(c["first_name"]) == first]
            if len(exact) == 1:
                match, confidence = exact[0], "exact"
            elif not exact and first:
                initial = [
                    c for c in candidates if normalize(c["first_name"])[:1] == first[:1]
                ]
                if len(initial) == 1:
                    match, confidence = initial[0], "initial"

            if match is None:
                stats["ambiguous" if len(candidates) > 1 else "unmatched"] += 1
                continue

            # Initial-only matches are review candidates, not verification-grade.
            if confidence == "initial" and not opts["include_initial"]:
                stats["initial_needs_review"] += 1
                review_candidates.append((faculty, match))
                continue

            changes = {}
            for field, value in (
                ("title", match["title"]),
                ("department", match["department"]),
                ("room", match["room"]),
                ("phone", match["phone_ext"]),
            ):
                if not value:
                    continue
                current = getattr(faculty, field) or ""
                if current and not overwrite:
                    continue
                if current != value:
                    changes[field] = value

            stats[f"matched_{confidence}"] += 1
            if changes:
                updates.append((faculty, match, changes, confidence))

            if opts["limit"] and len(updates) >= opts["limit"]:
                break

        self.stdout.write(
            f"matched_exact={stats['matched_exact']} matched_initial={stats['matched_initial']} "
            f"ambiguous={stats['ambiguous']} unmatched={stats['unmatched']}"
        )
        self.stdout.write(f"rows needing update: {len(updates)}")

        for faculty, match, changes, confidence in updates[:10]:
            self.stdout.write(
                f"  [{confidence}] {faculty.name or faculty.faculty_id}: "
                + ", ".join(f"{k}={v!r}" for k, v in changes.items())
            )

        if review_candidates:
            self.stdout.write(
                f"initial-only matches held for admin review: {len(review_candidates)}"
            )

        if not apply_changes:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        # Surface low-confidence candidates in the admin queue instead of writing them.
        for faculty, _match in review_candidates:
            if faculty.review_status != "pending":
                faculty.review_status = "pending"
                faculty.save(update_fields=["review_status", "updated_at"])

        now = timezone.now().astimezone(dt_timezone.utc)
        written = 0
        for faculty, match, changes, confidence in updates:
            for field, value in changes.items():
                setattr(faculty, field, value)

            faculty.directory_verified = confidence == "exact"
            faculty.save(update_fields=list(changes) + ["directory_verified", "updated_at"])
            written += 1

        self.stdout.write(self.style.SUCCESS(f"updated {written} faculty records"))
