"""Resolve each Faculty member's parent college/school from their department.

Source of truth: data/su_schools.json, extracted from salisbury.edu academic-offices pages.
Dry-run by default; use --apply to write.
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from academic.models import Faculty


def normalize(value):
    """Fold case, punctuation and the and/& spelling difference between sources."""
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"\band\b", " ", text)
    return re.sub(r"[^a-z]", "", text)


def first_token(value):
    tokens = re.findall(r"[a-z]+", (value or "").lower())
    tokens = [t for t in tokens if t not in {"the", "of", "and", "department", "school", "program"}]
    return tokens[0][:7] if tokens else ""


class Command(BaseCommand):
    help = "Populate Faculty.school from department using data/su_schools.json"

    def add_arguments(self, parser):
        parser.add_argument("--source", default="data/su_schools.json")
        parser.add_argument("--apply", action="store_true", help="Write changes")

    def handle(self, *args, **opts):
        path = Path(opts["source"])
        if not path.exists():
            raise CommandError(f"Mapping not found: {path}")

        mapping = json.loads(path.read_text())

        lookup = {}
        dept_names = {}
        for school, departments in mapping.items():
            for dept in departments:
                lookup[normalize(dept)] = school
                dept_names[dept] = school

        matched = 0
        unmatched = {}
        planned = []

        for member in Faculty.objects.exclude(department__isnull=True).exclude(department=""):
            dept_key = normalize(member.department)
            school = lookup.get(dept_key)

            if school is None:
                # Directory names differ slightly, e.g. "Marketing Department".
                for key, value in lookup.items():
                    if key and (key in dept_key or dept_key in key):
                        school = value
                        break

            if school is None:
                # "Mathematics" vs "Mathematical Sciences" style stem differences.
                stem = first_token(member.department)
                if stem:
                    for dept_name, value in dept_names.items():
                        if first_token(dept_name) == stem:
                            school = value
                            break

            if school is None:
                unmatched[member.department] = unmatched.get(member.department, 0) + 1
                continue

            matched += 1
            if member.school != school:
                planned.append((member, school))

        self.stdout.write(f"faculty with a department : {matched + sum(unmatched.values())}")
        self.stdout.write(f"resolved to a school      : {matched}")
        self.stdout.write(f"needing update            : {len(planned)}")

        if unmatched:
            self.stdout.write("\nunmatched departments:")
            for dept, count in sorted(unmatched.items(), key=lambda x: -x[1])[:10]:
                self.stdout.write(f"   {dept} ({count})")

        for member, school in planned[:8]:
            self.stdout.write(f"   {member.name or member.faculty_id}: {member.department} -> {school}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - re-run with --apply to write."))
            return

        for member, school in planned:
            member.school = school
            member.save(update_fields=["school", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"updated {len(planned)} faculty records"))
