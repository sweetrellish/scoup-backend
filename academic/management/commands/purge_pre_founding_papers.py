"""Purge unlinked papers that predate Salisbury University's 1925 founding.

Verification found something conclusive: a 1727 letter about "a physician at
Salisbury" was classified `genuine_su_unlinked` by verify_paper_institutions,
because OpenAlex itself tagged an author with the Salisbury University
institution ID. That is impossible - the university did not exist for another
198 years - so OpenAlex's institution attribution is unreliable for this sparse,
centuries-old metadata, regardless of what its own ID match claims.

This does not re-run verification or trust any external signal. The date field
alone is sufficient, hard evidence: any paper with zero linked Faculty and a
publication date before 1925 cannot be genuine Salisbury University research.

Dry-run by default; --apply deletes.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from academic.models import Paper

SU_FOUNDING_YEAR = 1925


class Command(BaseCommand):
    help = "Delete unlinked papers dated before Salisbury University's founding (1925)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete the matching papers")

    def handle(self, *args, **opts):
        qs = Paper.objects.annotate(n=Count("authors")).filter(
            n=0, date_published__year__lt=SU_FOUNDING_YEAR
        )
        candidates = list(qs.order_by("date_published"))

        self.stdout.write(f"unlinked papers before {SU_FOUNDING_YEAR}: {len(candidates)}")
        for p in candidates[:15]:
            self.stdout.write(f"  [{p.id}] {p.date_published} {p.title[:70]}")
        if len(candidates) > 15:
            self.stdout.write(f"  ...and {len(candidates) - 15} more")

        if opts["apply"]:
            deleted, _ = qs.delete()
            self.stdout.write(self.style.SUCCESS(f"deleted {deleted} papers"))
        else:
            self.stdout.write(self.style.WARNING(
                "DRY RUN - back up db.sqlite3, then re-run with --apply to delete."
            ))
