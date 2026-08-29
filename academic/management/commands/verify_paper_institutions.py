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
"""

import json
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand
from django.db.models import Count

from academic.management.commands.import_openalex import AuthorResolver
from academic.models import Paper

API = "https://api.openalex.org/works/https://doi.org/"
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
        if opts["max"]:
            qs = qs[: opts["max"]]

        resolver = AuthorResolver()
        session = requests.Session()
        params = {"mailto": opts["mailto"]}

        buckets = {
            "genuine_su_now_linked": [],
            "genuine_su_unlinked": [],
            "confirmed_foreign": [],
            "unverifiable": [],
            "errors": [],
        }

        checked = 0
        for paper in qs.iterator():
            checked += 1
            try:
                resp = session.get(API + paper.doi, params=params, timeout=15)
            except requests.RequestException as exc:
                buckets["errors"].append({"id": paper.id, "doi": paper.doi, "error": str(exc)})
                time.sleep(0.2)
                continue

            if resp.status_code == 404:
                buckets["unverifiable"].append({"id": paper.id, "doi": paper.doi, "reason": "not_on_openalex"})
                time.sleep(0.1)
                continue
            if resp.status_code != 200:
                buckets["errors"].append({"id": paper.id, "doi": paper.doi, "error": f"HTTP {resp.status_code}"})
                time.sleep(0.2)
                continue

            work = resp.json()
            authorships = work.get("authorships") or []
            all_institutions = [
                inst for a in authorships for inst in (a.get("institutions") or [])
            ]

            if not all_institutions:
                buckets["unverifiable"].append({"id": paper.id, "doi": paper.doi, "reason": "no_institution_data"})
                time.sleep(0.1)
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
                time.sleep(0.1)
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

            if checked % 200 == 0:
                self.stdout.write(f"  ...{checked}/{total_unlinked if not opts['max'] else min(total_unlinked, opts['max'])}")

            time.sleep(0.1)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(buckets, indent=2))

        self.stdout.write(self.style.MIGRATE_HEADING("verification results"))
        for key, items in buckets.items():
            self.stdout.write(f"  {key:<24} {len(items)}")
        self.stdout.write(f"\nreport written to {REPORT_PATH}")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to link rescued papers."))
