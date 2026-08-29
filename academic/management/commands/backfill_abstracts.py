"""Backfill missing Paper.abstract from OpenAlex, keyed by the DOI already stored.

3,630 papers (1,213 of them linked to real SU faculty) have no abstract, which
weakens search recall since abstract is a scored field. Every one of them has a
usable DOI already, so this looks each up individually rather than re-running the
institution-scoped bulk import.

Dry-run by default; --apply writes.
"""

import time

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q

from academic.management.commands.import_openalex import reconstruct_abstract
from academic.models import Paper

API = "https://api.openalex.org/works/https://doi.org/"


class Command(BaseCommand):
    help = "Backfill Paper.abstract from OpenAlex for papers that have a DOI but no abstract"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes")
        parser.add_argument("--max", type=int, default=None, help="Limit number of papers processed")
        parser.add_argument(
            "--only-affiliated",
            action="store_true",
            help="Restrict to papers with at least one linked Faculty author",
        )
        parser.add_argument(
            "--mailto",
            default="",
            help="Contact email for OpenAlex's polite pool (higher rate limit)",
        )

    def handle(self, *args, **opts):
        qs = Paper.objects.filter(Q(abstract__isnull=True) | Q(abstract="")).exclude(doi="")
        if opts["only_affiliated"]:
            qs = qs.filter(authors__isnull=False).distinct()
        qs = qs.order_by("id")
        if opts["max"]:
            qs = qs[: opts["max"]]

        session = requests.Session()
        params = {"mailto": opts["mailto"]} if opts["mailto"] else {}

        found = 0
        not_found = 0
        no_abstract_upstream = 0
        errors = 0
        total = qs.count()

        for i, paper in enumerate(qs.iterator(), start=1):
            try:
                resp = session.get(API + paper.doi, params=params, timeout=15)
            except requests.RequestException as exc:
                errors += 1
                self.stderr.write(f"  request failed for {paper.doi}: {exc}")
                time.sleep(0.2)
                continue

            if resp.status_code == 404:
                not_found += 1
                time.sleep(0.1)
                continue
            if resp.status_code != 200:
                errors += 1
                self.stderr.write(f"  HTTP {resp.status_code} for {paper.doi}")
                time.sleep(0.2)
                continue

            work = resp.json()
            abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
            if not abstract:
                no_abstract_upstream += 1
                time.sleep(0.1)
                continue

            found += 1
            if opts["apply"]:
                paper.abstract = abstract
                paper.save(update_fields=["abstract"])

            if i % 200 == 0:
                self.stdout.write(f"  ...{i}/{total} processed")

            time.sleep(0.1 if opts["mailto"] else 0.2)

        self.stdout.write(f"processed        : {min(total, opts['max'] or total)}")
        self.stdout.write(f"abstracts filled : {found}")
        self.stdout.write(f"not on OpenAlex  : {not_found}")
        self.stdout.write(f"no abstract there: {no_abstract_upstream}")
        self.stdout.write(f"errors           : {errors}")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to write."))
