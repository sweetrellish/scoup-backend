"""Cache the parsed SU directory to data/su_directory.json.

Parsing SUdirectory.pdf takes ~20 seconds, which is far too slow to do inside a
request. The admin review queue needs the directory rows to explain *why* a
faculty record could not be auto-verified, so the parse is cached here and read
by `academic.directory_match`.

Dry-run by default; --apply writes the file.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from academic.directory_match import DIRECTORY_CACHE, reset_cache
from academic.directory_parser import parse_directory


class Command(BaseCommand):
    help = "Parse SUdirectory.pdf and cache the rows to data/su_directory.json"

    def add_arguments(self, parser):
        parser.add_argument("--pdf", default="SUdirectory.pdf")
        parser.add_argument("--out", default=str(DIRECTORY_CACHE))
        parser.add_argument("--apply", action="store_true", help="Write the cache file")

    def handle(self, *args, **opts):
        try:
            rows = parse_directory(opts["pdf"])
        except FileNotFoundError:
            raise CommandError(f"Directory PDF not found: {opts['pdf']}")

        departments = {r["department"] for r in rows if r.get("department")}
        self.stdout.write(f"parsed rows: {len(rows)}")
        self.stdout.write(f"departments: {len(departments)}")
        for row in rows[:3]:
            self.stdout.write(f"  {row['first_name']} {row['last_name']} - {row['department']}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        out = Path(opts["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        reset_cache()
        self.stdout.write(self.style.SUCCESS(f"wrote {len(rows)} rows to {out}"))
