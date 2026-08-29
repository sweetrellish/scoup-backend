"""Backfill Paper records from OpenAlex for a given institution.

OpenAlex is free, CC0-licensed and has a documented API, so this replaces scraping.
Dry-run by default; use --apply to write.
"""

import re
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


class AuthorResolver:
    """Resolve an OpenAlex authorship to a Faculty row.

    Tries ORCID, then OpenAlex author id, then exact name, then last name plus
    first initial - which is what "A. Shakur" vs "Asif Shakur" requires.
    """

    def __init__(self):
        self.by_orcid = {}
        self.by_openalex = {}
        self.by_name = {}
        self.by_initial = {}

        for member in Faculty.objects.all():
            if member.orcid:
                self.by_orcid[self._norm_orcid(member.orcid)] = member
            if member.openalex_id:
                self.by_openalex[member.openalex_id] = member

            names = [
                (member.name or "").strip().lower(),
                f"{member.first_name or ''} {member.last_name or ''}".strip().lower(),
            ]
            for name in filter(None, names):
                self.by_name.setdefault(name, member)

            last = (member.last_name or "").strip().lower()
            first = (member.first_name or "").strip().lower()
            if not last and member.name:
                parts = member.name.split()
                if len(parts) > 1:
                    first, last = parts[0].lower(), parts[-1].lower()
            if last and first:
                # Ambiguous initials must not silently pick the wrong person.
                key = (last, first[0])
                if key in self.by_initial and self.by_initial[key] is not member:
                    self.by_initial[key] = None
                else:
                    self.by_initial.setdefault(key, member)

    @staticmethod
    def _norm_orcid(value):
        return (value or "").replace("https://orcid.org/", "").strip().upper()

    def resolve(self, authorship):
        author = authorship.get("author") or {}

        orcid = self._norm_orcid(author.get("orcid"))
        if orcid and orcid in self.by_orcid:
            return self.by_orcid[orcid], "orcid"

        oa_id = (author.get("id") or "").rsplit("/", 1)[-1]
        if oa_id and oa_id in self.by_openalex:
            return self.by_openalex[oa_id], "openalex"

        display = (author.get("display_name") or "").strip().lower()
        if display and display in self.by_name:
            return self.by_name[display], "name"

        parts = [p for p in re.split(r"[^a-z]+", display) if p]
        if len(parts) >= 2:
            match = self.by_initial.get((parts[-1], parts[0][0]))
            if match is not None:
                return match, "initial"

        return None, ""


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

        resolver = AuthorResolver()
        match_methods = {}

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
            su_authorships = []
            for authorship in work.get("authorships", []):
                if not any(
                    inst.get("id", "").endswith(institution)
                    for inst in authorship.get("institutions", [])
                ):
                    continue
                name = (authorship.get("author") or {}).get("display_name") or ""
                if name:
                    su_authors.append(name)
                    su_authorships.append(authorship)

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
            to_write.append((doi, fields, su_authorships))

            for authorship in su_authorships:
                member, method = resolver.resolve(authorship)
                if member is None:
                    new_authors.add((authorship.get("author") or {}).get("display_name") or "")
                else:
                    match_methods[method] = match_methods.get(method, 0) + 1

        self.stdout.write(f"institution        : {institution}")
        self.stdout.write(f"works fetched      : {created + updated}")
        self.stdout.write(f"  new papers       : {created}")
        self.stdout.write(f"  existing (update): {updated}")
        self.stdout.write(f"skipped (no DOI)   : {skipped_no_doi}")
        self.stdout.write(f"SU authors unknown to the DB: {len(new_authors)}")
        if match_methods:
            self.stdout.write(
                "resolved by -> " + ", ".join(f"{k}: {v}" for k, v in sorted(match_methods.items()))
            )

        for name in sorted(new_authors)[:10]:
            self.stdout.write(f"   unmatched author: {name}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        with transaction.atomic():
            for doi, fields, su_authorships in to_write:
                paper, _ = Paper.objects.update_or_create(doi=doi, defaults=fields)
                for authorship in su_authorships:
                    member, _method = resolver.resolve(authorship)
                    if member is None:
                        continue
                    paper.authors.add(member)
                    linked += 1

                    # Persist identity so future runs match on ORCID directly.
                    author = authorship.get("author") or {}
                    updates = []
                    orcid = AuthorResolver._norm_orcid(author.get("orcid"))
                    if orcid and not member.orcid:
                        member.orcid = orcid
                        updates.append("orcid")
                    oa_id = (author.get("id") or "").rsplit("/", 1)[-1]
                    if oa_id and not member.openalex_id:
                        member.openalex_id = oa_id
                        updates.append("openalex_id")
                    if updates:
                        member.save(update_fields=updates + ["updated_at"])

        self.stdout.write(
            self.style.SUCCESS(f"wrote {len(to_write)} papers; linked {linked} author records")
        )
