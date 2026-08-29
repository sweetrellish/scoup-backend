"""Delete papers confirmed foreign by `verify_paper_institutions`.

Reads data/paper_verification_report.json - never re-derives the categorization
itself, so this can only remove what verification already confirmed has no
Salisbury University author (by authoritative OpenAlex institution ID, not a
text match on "Salisbury"). Only touches the `confirmed_foreign` bucket; every
other bucket (genuine_su_unlinked, unverifiable, errors) is left untouched,
because "we can't confirm it's foreign" is not the same as "it's foreign."

Dry-run by default; --apply deletes. Always back up db.sqlite3 before --apply -
this is the one command in this project that removes Paper rows outright.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from academic.models import Paper

REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "paper_verification_report.json"


class Command(BaseCommand):
    help = "Delete papers the verification report confirmed have no Salisbury University author"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete the confirmed_foreign papers")
        parser.add_argument(
            "--report",
            default=str(REPORT_PATH),
            help="Path to the verification report (default: data/paper_verification_report.json)",
        )

    def handle(self, *args, **opts):
        report_path = Path(opts["report"])
        if not report_path.exists():
            raise CommandError(f"No report at {report_path} - run verify_paper_institutions first")

        report = json.loads(report_path.read_text())
        candidates = report.get("confirmed_foreign", [])
        if not candidates:
            self.stdout.write("Nothing to purge - confirmed_foreign is empty.")
            return

        ids = [c["id"] for c in candidates]
        qs = Paper.objects.filter(pk__in=ids)
        found = qs.count()

        self.stdout.write(f"report candidates       : {len(ids)}")
        self.stdout.write(f"still present in DB      : {found}")
        for c in candidates[:10]:
            places = ", ".join(c.get("institutions", [])) or "no institution listed"
            self.stdout.write(f"  [{c['id']}] {c.get('title', '')[:70]} -- {places}")
        if len(candidates) > 10:
            self.stdout.write(f"  ...and {len(candidates) - 10} more")

        if opts["apply"]:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.SUCCESS(f"deleted {deleted} papers"))
        else:
            self.stdout.write(self.style.WARNING(
                "DRY RUN - back up db.sqlite3, then re-run with --apply to delete."
            ))
