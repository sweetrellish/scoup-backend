"""Cross-reference the `genuine_su_unlinked` bucket against the real SU directory.

`verify_paper_institutions` trusts OpenAlex's own institution tag, which proved
unreliable on its own: a 1727 letter and papers for authors like Séverine Tasker
(a lifelong UK veterinary researcher) were tagged "Salisbury University" based on
a single, unsupported year with no other corroboration. That is exactly the kind
of single-signal trust this project has been burned by twice now.

This applies the same two-signal standard that safely created the original 87
dual-source faculty (2026-08-29 11:03): OpenAlex institution tag AND a real,
unambiguous match in the parsed SU directory (data/su_directory.json, 1,849
entries from the official PDF) must both agree. Ambiguous name matches (shared
surname + initial across different real people) resolve to "no match", never a
guess - the same rule as every other importer in this project.

Re-fetches only the ~6,053 papers already in `genuine_su_unlinked` (not the
whole corpus), this time recording which specific author triggered the SU tag
so it can be checked against the directory. Writes
data/directory_crossref_report.json. Dry-run only - never deletes; use
purge_unverified_papers or a follow-up command to act on `no_directory_match`.
"""

import json
import re
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError

REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "paper_verification_report.json"
DIRECTORY_PATH = Path(__file__).resolve().parents[3] / "data" / "su_directory.json"
OUTPUT_PATH = Path(__file__).resolve().parents[3] / "data" / "directory_crossref_report.json"

API = "https://api.openalex.org/works"
SU_OPENALEX_ID = "https://openalex.org/I9364636"
SU_ROR = "029gwvs11"
BATCH_SIZE = 50


def _is_su_institution(inst):
    if not inst:
        return False
    if inst.get("id") == SU_OPENALEX_ID:
        return True
    if SU_ROR in (inst.get("ror") or ""):
        return True
    return (inst.get("display_name") or "").strip().lower() == "salisbury university"


class DirectoryLookup:
    """Exact first+last name match only.

    An earlier version of this check also matched on last-name + first-initial,
    and it produced exactly the false positive this project has already been
    burned by once (Shing Yip Lee, 2026-08-29 00:19): "Karren Lewis" resolved to
    "Kayonna Lewis", and "Mark G. Treuth" resolved to "Margarita Treuth" - two
    different real people who happen to share a surname and first letter. An
    initial match is not evidence, it is a guess; dropped entirely.
    """

    def __init__(self, entries):
        self.by_exact = {}
        for e in entries:
            last = (e.get("last_name") or "").strip().lower()
            first = (e.get("first_name") or "").strip().lower()
            if not last or not first:
                continue
            self.by_exact[(last, first)] = e

    def match(self, display_name):
        parts = [p for p in re.split(r"[^a-zA-Z]+", display_name or "") if p]
        if len(parts) < 2:
            return None, ""
        first, last = parts[0].lower(), parts[-1].lower()
        exact = self.by_exact.get((last, first))
        if exact:
            return exact, "exact"
        # Safe, deterministic formatting normalization (not a guess about identity):
        # a middle name concatenated onto the first in the directory export, e.g.
        # OpenAlex "Sook Hyun Kim" vs directory "SookHyun Kim".
        if len(parts) >= 3:
            joined = "".join(p.lower() for p in parts[:-1])
            joined_match = self.by_exact.get((last, joined))
            if joined_match:
                return joined_match, "exact_joined_middle"
        return None, ""


class Command(BaseCommand):
    help = "Cross-reference genuine_su_unlinked papers against the real SU directory"

    def add_arguments(self, parser):
        parser.add_argument("--max", type=int, default=None)
        parser.add_argument("--mailto", default="frenchiery817@gmail.com")

    def handle(self, *args, **opts):
        if not REPORT_PATH.exists():
            raise CommandError(f"No report at {REPORT_PATH} - run verify_paper_institutions first")
        if not DIRECTORY_PATH.exists():
            raise CommandError(f"No SU directory at {DIRECTORY_PATH}")

        report = json.loads(REPORT_PATH.read_text())
        candidates = report.get("genuine_su_unlinked", [])
        if opts["max"]:
            candidates = candidates[: opts["max"]]

        directory = DirectoryLookup(json.loads(DIRECTORY_PATH.read_text()))

        session = requests.Session()
        results = {"directory_match": [], "no_directory_match": [], "unresolved": []}

        batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
        checked = 0
        for batch in batches:
            by_doi = {c["doi"].lower(): c for c in batch}
            filt = "doi:" + "|".join(c["doi"] for c in batch)
            try:
                resp = session.get(
                    API, params={"filter": filt, "per-page": BATCH_SIZE, "mailto": opts["mailto"]}, timeout=30
                )
            except requests.RequestException as exc:
                for c in batch:
                    results["unresolved"].append({**c, "reason": str(exc)})
                checked += len(batch)
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "?")
                self.stderr.write(self.style.ERROR(
                    f"OpenAlex quota exhausted after {checked}/{len(candidates)}. Retry-After: {retry_after}s."
                ))
                break
            if resp.status_code != 200:
                for c in batch:
                    results["unresolved"].append({**c, "reason": f"HTTP {resp.status_code}"})
                checked += len(batch)
                continue

            found = {}
            for work in resp.json().get("results", []):
                doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
                found[doi] = work

            for doi_key, candidate in by_doi.items():
                checked += 1
                work = found.get(doi_key)
                if work is None:
                    results["unresolved"].append({**candidate, "reason": "not_on_openalex_now"})
                    continue

                su_authors = [
                    a for a in (work.get("authorships") or [])
                    if any(_is_su_institution(i) for i in (a.get("institutions") or []))
                ]
                if not su_authors:
                    results["unresolved"].append({**candidate, "reason": "no_su_tag_this_time"})
                    continue

                matched_any = False
                triggering_names = []
                for a in su_authors:
                    name = (a.get("author") or {}).get("display_name", "")
                    triggering_names.append(name)
                    entry, how = directory.match(name)
                    if entry:
                        matched_any = True
                        results["directory_match"].append({
                            **candidate,
                            "openalex_author": name,
                            "directory_match": f"{entry['first_name']} {entry['last_name']}",
                            "directory_department": entry.get("department", ""),
                            "match_type": how,
                        })
                        break

                if not matched_any:
                    results["no_directory_match"].append({
                        **candidate,
                        "openalex_authors_checked": triggering_names,
                    })

            self.stdout.write(f"  ...{checked}/{len(candidates)}")
            time.sleep(0.2)

        OUTPUT_PATH.write_text(json.dumps(results, indent=2))
        self.stdout.write(self.style.MIGRATE_HEADING("cross-reference results"))
        for key, items in results.items():
            self.stdout.write(f"  {key:<22} {len(items)}")
        self.stdout.write(f"\nreport written to {OUTPUT_PATH}")
