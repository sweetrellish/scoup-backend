"""Recompute denormalized Faculty metrics from linked papers.

`article_count`, `total_citations` and `average_citations` are stored on Faculty but
were populated by the original import. Ingesting new papers does not refresh them, so
they drift (e.g. a profile showing 1 paper while 31 are linked).

Safe to run repeatedly; intended for the scheduled validation worker.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from academic.models import Faculty


class Command(BaseCommand):
    help = "Recalculate Faculty article_count / total_citations / average_citations"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes")
        parser.add_argument(
            "--only-affiliated",
            action="store_true",
            help="Restrict to SU-affiliated records (skip external co-authors)",
        )

    def handle(self, *args, **opts):
        qs = Faculty.objects.annotate(
            linked_papers=Count("papers", distinct=True),
            linked_citations=Sum("papers__tc_count"),
        )
        if opts["only_affiliated"]:
            from academic.views import _su_affiliated

            qs = qs.filter(_su_affiliated())

        drifted = []
        for member in qs:
            papers = member.linked_papers or 0
            citations = member.linked_citations or 0
            average = round(citations / papers, 2) if papers else 0.0

            if (
                member.article_count != papers
                or member.total_citations != citations
                or abs((member.average_citations or 0.0) - average) > 0.01
            ):
                drifted.append((member, papers, citations, average))

        self.stdout.write(f"records examined : {qs.count()}")
        self.stdout.write(f"records drifted  : {len(drifted)}")

        for member, papers, citations, _avg in drifted[:10]:
            self.stdout.write(
                f"   {(member.name or member.faculty_id)[:34]:36} "
                f"papers {member.article_count} -> {papers} | "
                f"citations {member.total_citations} -> {citations}"
            )

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        for member, papers, citations, average in drifted:
            member.article_count = papers
            member.total_citations = citations
            member.average_citations = average
            member.save(
                update_fields=[
                    "article_count",
                    "total_citations",
                    "average_citations",
                    "updated_at",
                ]
            )

        self.stdout.write(self.style.SUCCESS(f"updated {len(drifted)} records"))
