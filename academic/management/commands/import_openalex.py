"""Backfill Paper records from OpenAlex for a given institution.

OpenAlex is free, CC0-licensed and has a documented API, so this replaces scraping.
Dry-run by default; use --apply to write.
"""

import time
from datetime import date

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academic.models import Faculty, Paper

API = "https://api.openalex.org/works"
SU_INSTITUTION_ID = "I9364636"  # Salisbury University


def reconstruct_abstract(inverted_index):
    """OpenAlex ships abstracts as {word: [positions]}; rebuild reading order."""
    if not inverted_index:
        return ""
    positions = []
    for word, spots in inverted_index.items():
        for spot in spots:
            positions.append((spot, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def normalize_doi(value):
    if not value:
        return ""
    return value.replace("https://doi.org/", "").strip().lower()


def parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = "Backfill papers from OpenAlex for an institution"

    def add_arguments(self, parser):
        parser.add_argument("--institution", default=SU_INSTITUTION_ID)
        parser.add_argument("--since", default="", help="Only works from this date (YYYY-MM-DD)")
        parser.add_argument("--max", type=int, default=0, help="Stop after N works")
        parser.add_argument("--apply", action="store_true", help="Write to the database")
        parser.add_argument(
            "--mailto",
            default="frenchiery817@gmail.com",
            help="Contact address for the OpenAlex polite pool",
        )

    def _iter_works(self, institution, since, mailto, max_works):
        cursor = "*"
        seen = 0
        filters = f"institutions.id:{institution}"
        if since:
            filters += f",from_publication_date:{since}"

        while cursor:
            params = {
                "filter": filters,
                "per-page": 200,
                "cursor": cursor,
                "mailto": mailto,
            }
            try:
                res = requests.get(API, params=params, timeout=60)
                res.raise_for_status()
            except requests.RequestException as exc:
                raise CommandError(f"OpenAlex request failed: {exc}")

            payload = res.json()
            for work in payload.get("results", []):
                yield work
                seen += 1
                if max_works and seen >= max_works:
                    return

            cursor = (payload.get("meta") or {}).get("next_cursor")
            time.sleep(0.2)  # stay well inside the polite-pool rate limit

    def handle(self, *args, **opts):
        institution = opts["institution"]
        mailto = opts["mailto"]

        # Index existing faculty by name so SU authorships can be linked.
        faculty_by_name = {}
        for member in Faculty.objects.all():
            for key in filter(None, [
                (member.name or "").strip().lower(),
                f"{member.first_name or ''} {member.last_name or ''}".strip().lower(),
            ]):
                faculty_by_name.setdefault(key, member)

        existing = set(Paper.objects.values_list("doi", flat=True))

        created = updated = skipped_no_doi = linked = 0
        new_authors = set()
        to_write = []

        for work in self._iter_works(institution, opts["since"], mailto, opts["max"]):
            doi = normalize_doi(work.get("doi"))
            if not doi:
                skipped_no_doi += 1
                continue

            concepts = [c["display_name"] for c in work.get("concepts", []) if c.get("display_name")]
            topics = [t["display_name"] for t in work.get("topics", []) if t.get("display_name")]

            source = (work.get("primary_location") or {}).get("source") or {}
            best_oa = work.get("best_oa_location") or {}

            su_authors = []
            for authorship in work.get("authorships", []):
                if not any(
                    inst.get("id", "").endswith(institution)
                    for inst in authorship.get("institutions", [])
                ):
                    continue
                name = (authorship.get("author") or {}).get("display_name") or ""
                if name:
                    su_authors.append(name)

            fields = {
                "title": (work.get("title") or "")[:500],
                "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
                "journal": (source.get("display_name") or "")[:255],
                "date_published": parse_date(work.get("publication_date")),
                "tc_count": work.get("cited_by_count") or 0,
                "url": work.get("doi") or "",
                "download_url": best_oa.get("pdf_url") or "",
                "keywords": concepts,
                "themes": topics,
                "faculty_members": su_authors,
            }

            if doi in existing:
                updated += 1
            else:
                created += 1
            to_write.append((doi, fields, su_authors))

            for name in su_authors:
                if name.strip().lower() not in faculty_by_name:
                    new_authors.add(name)

        self.stdout.write(f"institution        : {institution}")
        self.stdout.write(f"works fetched      : {created + updated}")
        self.stdout.write(f"  new papers       : {created}")
        self.stdout.write(f"  existing (update): {updated}")
        self.stdout.write(f"skipped (no DOI)   : {skipped_no_doi}")
        self.stdout.write(f"SU authors unknown to the DB: {len(new_authors)}")

        for name in sorted(new_authors)[:10]:
            self.stdout.write(f"   unmatched author: {name}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        with transaction.atomic():
            for doi, fields, su_authors in to_write:
                paper, _ = Paper.objects.update_or_create(doi=doi, defaults=fields)
                for name in su_authors:
                    member = faculty_by_name.get(name.strip().lower())
                    if member:
                        paper.authors.add(member)
                        linked += 1

        self.stdout.write(
            self.style.SUCCESS(f"wrote {len(to_write)} papers; linked {linked} author records")
        )
