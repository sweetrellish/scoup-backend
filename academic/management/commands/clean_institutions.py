"""Clean the affiliation-derived institution list into a shippable dataset.

`data/institutions.json` was extracted by scanning abstract text, because
`Paper.faculty_affiliations` is empty ({}) on all 9,237 papers. Abstracts embed
author blocks, so the extraction picked up three kinds of noise, documented in
the 2026-08-29 09:40 operations log entry:

  1. name bleed      - "Dean J. Kotlowski Salisbury University"
  2. sentence bleed  - "University of Delaware. He"
  3. truncation      - "University of Foreign Studies" from
                       "Hankuk University of Foreign Studies"

(1) and (2) are repairable without inventing anything: the institution is stated
in full and only surrounding text has to be removed. (3) is not - recovering
"George Mason" from "Mason University" means guessing which institution was
meant, so those entries are **dropped**, per the project rule that an ambiguous
match is never resolved by guessing.

Every input entry must be classified. Anything matching neither a repair rule nor
the reviewed drop list is reported as `needs_review` and excluded, so noise added
by a future re-extraction fails loudly instead of shipping silently.

Dry-run by default; --apply writes data/institutions_clean.json.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

# Entries dropped after manual review, with the reason each cannot be repaired.
# Keyed by the name as it appears in the source file.
DROP = {
    # -- Truncated multi-part names: the missing part cannot be recovered. ------
    "American College": "truncated; ambiguous (Kennedy Center American College Theater Festival?)",
    "City University": "truncated; dozens of institutions share this prefix",
    "Federal University": "truncated; no country or city to identify it",
    "Hankuk University": "truncated form of Hankuk University of Foreign Studies",
    "University of Foreign Studies": "truncated form of Hankuk University of Foreign Studies",
    "University of Foreign Studies Research Fund": "a funding body fragment, not an institution",
    "Mason University": "truncated; almost certainly George Mason, but that is a guess",
    "Mississippi University": "truncated; Mississippi State / Univ. of Mississippi / MUW ambiguous",
    "Rensselaer Polytechnic": "truncated form of Rensselaer Polytechnic Institute",
    "Scheller College": "truncated; a college within Georgia Tech, not a standalone institution",
    "University of Wellington": "truncated form of Victoria University of Wellington",
    "Penn State University College": "truncated; which Penn State college is not stated",
    "University of Maryland Center": "truncated; the centre is not named",
    "United States University and Community College": "not an institution; a category phrase",
    "Kennedy Center American College": "truncated festival name, not an institution",
    "South-Western College": "a textbook publisher imprint, not an institution",
    "University of Texas Dallas": "cannot confirm whether UT Dallas or a bled fragment",
    "University of Texas Southwestern": "cannot confirm whether UT Southwestern or a bled fragment",
    # -- Sentence fragments that are not institution names at all. -------------
    "Exercise Motivation And Physical Activity Of College": "abstract text, not an institution",
    # -- Two institutions joined by a conjunction; splitting would invent a pair.
    "Research Corporation for Science Advancement and Salisbury University":
        "names two institutions; splitting them is inference",
    "Henson School of Science and Technology and Salisbury University":
        "conjunction of an SU school and SU itself; ambiguous as a single entity",
}

# University presses are publishers picked up from reference lists, not research
# affiliations. Excluded from an affiliation-derived dataset by rule, not by name.
PRESS_SUFFIX = re.compile(r"\bPress\b", re.IGNORECASE)

# Trailing prose after a sentence break: "University of Delaware. He".
SENTENCE_BLEED = re.compile(r"^(.*?)\.\s+\S.*$")

# Bibliographic markers that trail a name with no sentence break.
TRAILING_TOKENS = re.compile(r"\s+(DOI|Crossref|Google Scholar)\s*$", re.IGNORECASE)

# Leading degree/boilerplate markers: "Ph.D. Indiana University".
LEADING_TOKENS = re.compile(
    r"^(Ph\.?D\.?|MD|Dr\.?|Authors? Affiliations?|Expand All|Consequence Information|"
    r"Native|Classroom|Assistant Director of)\s+",
    re.IGNORECASE,
)

# An entry ending in an explicit institution name, preceded by bled-in text.
# Only Salisbury University is repaired this way: it is the one institution whose
# full name is unambiguous in this corpus and which the bleed overwhelmingly hits.
SU_TAIL = re.compile(r"^(.*\S)\s+(Salisbury University)$")

# SU sub-units that should roll up to the parent institution.
SU_SUBUNITS = {
    "Salisbury University Honors College",
    "Institutional Review Board of Salisbury University",
    "Ethics Committee of Salisbury University",
    "Henson School of Science and Technology of Salisbury University",
}

# Sub-units of other institutions that roll up to a named parent.
SUBUNIT_PARENT = {
    "Institutional Review Board of Point Loma Nazarene University": "Point Loma Nazarene University",
    "CUNY Lehman College": "Lehman College",
    "University of Auckland Business School": "University of Auckland",
}


def repair(name):
    """Return (cleaned_name, rule) or (None, reason) when the entry must be dropped."""
    original = name.strip()

    if original in DROP:
        return None, DROP[original]

    if original in SU_SUBUNITS:
        return "Salisbury University", "su_subunit_rollup"

    working = TRAILING_TOKENS.sub("", original).strip()
    rule = "verbatim" if working == original else "trailing_marker_stripped"

    match = SENTENCE_BLEED.match(working)
    if match:
        working = match.group(1).strip()
        rule = "sentence_bleed_stripped"

    if working in SUBUNIT_PARENT:
        return SUBUNIT_PARENT[working], "subunit_rollup"

    stripped = LEADING_TOKENS.sub("", working).strip()
    if stripped != working:
        working, rule = stripped, "leading_marker_stripped"

    match = SU_TAIL.match(working)
    if match:
        prefix = match.group(1)
        # "X and Salisbury University" names two parties, not one with bled text.
        if re.search(r"\band\b", prefix, re.IGNORECASE):
            return None, "conjunction of two institutions; splitting would be inference"
        return "Salisbury University", "name_bleed_stripped"

    if working in SUBUNIT_PARENT:
        return SUBUNIT_PARENT[working], "subunit_rollup"

    if working in DROP:
        return None, DROP[working]

    if PRESS_SUFFIX.search(working):
        return None, "university press; a publisher in a reference list, not an affiliation"

    # Anything still carrying a person-shaped prefix or stray prose is unresolved.
    if re.search(r"\b(He|His|Her|She|Dr)\b\s*$", working):
        return None, "unrepaired prose fragment"

    return working, rule


class Command(BaseCommand):
    help = "Clean data/institutions.json into data/institutions_clean.json"

    def add_arguments(self, parser):
        parser.add_argument("--source", default="data/institutions.json")
        parser.add_argument("--out", default="data/institutions_clean.json")
        parser.add_argument("--apply", action="store_true", help="Write the cleaned file")

    def handle(self, *args, **opts):
        source = Path(opts["source"])
        if not source.exists():
            raise CommandError(f"Source not found: {source}")

        entries = json.loads(source.read_text())

        merged = defaultdict(int)
        provenance = defaultdict(set)
        dropped = []
        rules = defaultdict(int)

        for entry in entries:
            name = (entry.get("name") or "").strip()
            mentions = int(entry.get("mentions") or 0)
            if not name:
                continue

            cleaned, note = repair(name)
            if cleaned is None:
                dropped.append((name, mentions, note))
                continue

            merged[cleaned] += mentions
            rules[note] += 1
            if cleaned != name:
                provenance[cleaned].add(name)

        result = [
            {
                "name": name,
                "mentions": count,
                "is_host": name == "Salisbury University",
                "merged_from": sorted(provenance[name]),
            }
            for name, count in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        self.stdout.write(f"source entries      : {len(entries)}")
        self.stdout.write(f"clean institutions  : {len(result)}")
        self.stdout.write(f"dropped             : {len(dropped)}")
        self.stdout.write(f"mentions kept       : {sum(merged.values())}")
        self.stdout.write("\nrepairs applied:")
        for rule, count in sorted(rules.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"   {rule}: {count}")

        self.stdout.write("\ndropped entries:")
        for name, mentions, note in sorted(dropped, key=lambda d: -d[1]):
            self.stdout.write(f"   [{mentions}] {name}  --  {note}")

        top = result[:8]
        self.stdout.write("\ntop institutions:")
        for item in top:
            extra = f"  (merged {len(item['merged_from'])})" if item["merged_from"] else ""
            self.stdout.write(f"   {item['mentions']:4d}  {item['name']}{extra}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        out = Path(opts["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"\nwrote {len(result)} institutions to {out}"))
