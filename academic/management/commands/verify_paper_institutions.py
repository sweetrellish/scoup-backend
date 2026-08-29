"""Verify unlinked papers actually belong to Salisbury University, by institution ID.

Text-matching on the word "Salisbury" (the original `import_full_dataset` pipeline
had no institution filter at all) pulled in papers from Salisbury, England and
Salisbury, Australia. This checks the *institution*, not the place name: each
paper with zero linked Faculty is looked up on OpenAlex by its own DOI, and its
authorships' institutions are matched against Salisbury University's authoritative
OpenAlex ID / ROR - never against the text "Salisbury".

Categories:
  - genuine_su_now_linked : an SU-institution author was found and resolved to an
                            existing Faculty record (ORCID/OpenAlex id/name/initial) -
                            linked, rescuing a paper that failed to link on import.
  - genuine_su_unlinked   : an SU-institution author was found but does not match any
                            existing Faculty record - kept, not deleted, flagged for
                            a future faculty-creation pass.
  - confirmed_foreign     : institutions were listed for every author and NONE is
                            Salisbury University - candidate for removal.
  - unverifiable          : no institution data available at all (or DOI not found
                            on OpenAlex) - kept, not deleted; cannot confirm either way.
  - errors                : the request itself failed - kept, not deleted.

Writes a JSON report to data/paper_verification_report.json. Dry-run by default;
--apply links rescued papers to Faculty. Deletion is a separate step
(purge_unverified_papers) that reads this report - verification never deletes.

Uses OpenAlex's batch `filter=doi:a|b|c` (up to BATCH_SIZE DOIs per request)
instead of one DOI per request - one-by-one exhausted the daily rate/credit
budget after a few thousand calls (HTTP 429, "Insufficient budget... Resets at
midnight UTC") long before finishing. If that happens again, this command
stops immediately (rather than burning the rest of the run on 429s) and reports
how far it got plus the Retry-After time, so a re-run after the reset resumes
cleanly - already-linked papers are excluded by the queryset automatically.
"""

import json
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db.models import Count

from academic.management.commands.import_openalex import AuthorResolver
from academic.models import Paper

API = "https://api.openalex.org/works"
BATCH_SIZE = 50
SU_OPENALEX_ID = "https://openalex.org/I9364636"
SU_ROR = "029gwvs11"

REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "paper_verification_report.json"


def _is_su_institution(inst):
    if not inst:
        return False
    if inst.get("id") == SU_OPENALEX_ID:
        return True
    if SU_ROR in (inst.get("ror") or ""):
        return True
    return (inst.get("display_name") or "").strip().lower() == "salisbury university"


class Command(BaseCommand):
    help = "Verify unlinked papers against Salisbury University's OpenAlex institution ID"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Link rescued papers to Faculty")
        parser.add_argument("--max", type=int, default=None, help="Limit number of papers checked")
        parser.add_argument("--mailto", default="frenchiery817@gmail.com")

    def handle(self, *args, **opts):
        qs = (
            Paper.objects.annotate(n=Count("authors"))
            .filter(n=0)
            .exclude(doi="")
            .order_by("id")
        )
        total_unlinked = qs.count()
        papers = list(qs[: opts["max"]] if opts["max"] else qs)

        resolver = AuthorResolver()
        session = requests.Session()

        buckets = {
            "genuine_su_now_linked": [],
            "genuine_su_unlinked": [],
            "confirmed_foreign": [],
            "unverifiable": [],
            "errors": [],
        }

        checked = 0
        batches = [papers[i : i + BATCH_SIZE] for i in range(0, len(papers), BATCH_SIZE)]

        for batch in batches:
            by_doi = {p.doi.lower(): p for p in batch}
            filt = "doi:" + "|".join(p.doi for p in batch)
            try:
                resp = session.get(
                    API,
                    params={"filter": filt, "per-page": BATCH_SIZE, "mailto": opts["mailto"]},
                    timeout=30,
                )
            except requests.RequestException as exc:
                for p in batch:
                    buckets["errors"].append({"id": p.id, "doi": p.doi, "error": str(exc)})
                checked += len(batch)
                continue

            if resp.status_code == 429:
                body = {}
                try:
                    body = resp.json()
                except ValueError:
                    pass
                retry_after = resp.headers.get("Retry-After", "?")
                self.stderr.write(self.style.ERROR(
                    f"OpenAlex quota exhausted after checking {checked}/{len(papers)} "
                    f"({total_unlinked} total unlinked). {body.get('message', '')} "
                    f"Retry-After: {retry_after}s."
                ))
                self._write_report(buckets)
                raise CommandError(
                    f"Stopped early: rate/credit limit hit. Retry after {retry_after}s "
                    "and re-run - already-linked papers are skipped automatically."
                )
            if resp.status_code != 200:
                for p in batch:
                    buckets["errors"].append({"id": p.id, "doi": p.doi, "error": f"HTTP {resp.status_code}"})
                checked += len(batch)
                time.sleep(0.3)
                continue

            found_by_doi = {}
            for work in resp.json().get("results", []):
                doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
                found_by_doi[doi] = work

            for doi_key, paper in by_doi.items():
                checked += 1
                work = found_by_doi.get(doi_key)
                if work is None:
                    buckets["unverifiable"].append({"id": paper.id, "doi": paper.doi, "reason": "not_on_openalex"})
                    continue

                authorships = work.get("authorships") or []
                all_institutions = [i for a in authorships for i in (a.get("institutions") or [])]

                if not all_institutions:
                    buckets["unverifiable"].append({"id": paper.id, "doi": paper.doi, "reason": "no_institution_data"})
                    continue

                su_authorships = [
                    a for a in authorships if any(_is_su_institution(i) for i in (a.get("institutions") or []))
                ]

                if not su_authorships:
                    sample_places = sorted(
                        {i.get("display_name", "") for i in all_institutions if i.get("display_name")}
                    )[:3]
                    buckets["confirmed_foreign"].append(
                        {"id": paper.id, "doi": paper.doi, "title": paper.title, "institutions": sample_places}
                    )
                    continue

                linked = False
                for authorship in su_authorships:
                    match, _via = resolver.resolve(authorship)
                    if match is not None:
                        linked = True
                        if opts["apply"]:
                            paper.authors.add(match)
                        break

                if linked:
                    buckets["genuine_su_now_linked"].append({"id": paper.id, "doi": paper.doi})
                else:
                    buckets["genuine_su_unlinked"].append({"id": paper.id, "doi": paper.doi, "title": paper.title})

            self.stdout.write(f"  ...{checked}/{len(papers)}")
            time.sleep(0.2)

        self._write_report(buckets)
        self.stdout.write(self.style.MIGRATE_HEADING("verification results"))
        for key, items in buckets.items():
            self.stdout.write(f"  {key:<24} {len(items)}")
        self.stdout.write(f"\nreport written to {REPORT_PATH}")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to link rescued papers."))

    def _write_report(self, buckets):
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(buckets, indent=2))
