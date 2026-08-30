"""Cache keyword document frequency for search-relevance scoring.

OpenAlex's automatic concept tagging attaches broad umbrella fields (Biology,
Computer science, Political science...) to a huge share of the corpus - "Computer
science" alone sits on 2,689 of 9,038 papers (30%). A single word like "computer"
matching one of these coarse tags is far weaker evidence of true topical relevance
than a specific keyword that appears on only a handful of papers, the same reason
TF-IDF down-weights common terms in classic information retrieval.

Writes data/keyword_document_frequency.json, loaded once at process start by
academic/views.py. Safe to run repeatedly (e.g. after each OpenAlex import) -
frequencies simply drift with the corpus; there is no --apply flag because this
never touches the database, only a cache file used for scoring.
"""

import collections
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from academic.models import Paper

CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "keyword_document_frequency.json"


class Command(BaseCommand):
    help = "Rebuild the keyword document-frequency cache used to discount broad umbrella tags"

    def handle(self, *args, **opts):
        counts = collections.Counter()
        total = 0
        for keywords in Paper.objects.values_list("keywords", flat=True).iterator():
            total += 1
            for k in keywords or []:
                counts[str(k).strip().lower()] += 1

        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps({"total_papers": total, "counts": dict(counts)}))

        top = counts.most_common(10)
        self.stdout.write(f"papers scanned    : {total}")
        self.stdout.write(f"distinct keywords : {len(counts)}")
        self.stdout.write("most common (share of corpus):")
        for k, c in top:
            self.stdout.write(f"  {c:>5} ({c / total:.0%})  {k}")
        self.stdout.write(f"\ncache written to {CACHE_PATH}")
