"""Scheduled data validation worker.

Runs the ingest + repair jobs in dependency order, then audits the database for
conditions that need a human decision. Jobs repair what is safely derivable;
audits only report, because guessing is what produced the bad data this worker
exists to catch.

Dry-run by default. `--apply` writes. Designed for a systemd timer.
"""

from __future__ import annotations

import fcntl
import json
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Count, Q, Sum
from django.utils import timezone

# settings.BASE_DIR points at the settings package, not the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = PROJECT_ROOT / "data" / "validation_state.json"
LOCK_FILE = Path("/tmp/scoup-validation.lock")

# (name, command, args, needs_apply). Order is the dependency order:
# new papers must land before metrics are recomputed from them.
JOBS = [
    ("openalex", "import_openalex", {}, True),
    ("metrics", "recalc_faculty_metrics", {}, True),
    ("schools", "import_su_schools", {}, True),
]


class Command(BaseCommand):
    help = "Run scheduled ingest, repair and audit passes over the database"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes")
        parser.add_argument(
            "--jobs",
            default="",
            help="Comma-separated subset of: " + ",".join(j[0] for j in JOBS),
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Ingest the whole corpus instead of only works since the last run",
        )
        parser.add_argument("--audit-only", action="store_true", help="Skip ingest jobs")
        parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    def handle(self, *args, **opts):
        lock = LOCK_FILE.open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stderr.write(self.style.WARNING("another validation run is in progress; exiting"))
            return

        started = timezone.now()
        state = self._load_state()
        apply_changes = opts["apply"]
        report = {
            "started": started.isoformat(),
            "mode": "apply" if apply_changes else "dry-run",
            "jobs": {},
            "audits": {},
        }

        if not opts["audit_only"]:
            wanted = {j.strip() for j in opts["jobs"].split(",") if j.strip()}
            for name, command, extra, needs_apply in JOBS:
                if wanted and name not in wanted:
                    continue
                report["jobs"][name] = self._run_job(
                    name, command, extra, needs_apply, apply_changes, state, opts["full"]
                )

        report["audits"] = self._audit()
        report["finished"] = timezone.now().isoformat()
        report["duration_seconds"] = round((timezone.now() - started).total_seconds(), 1)

        if apply_changes:
            state["last_run"] = started.isoformat()
            state["last_report"] = report
            self._save_state(state)

        if opts["json"]:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._render(report)

        fcntl.flock(lock, fcntl.LOCK_UN)

    def _run_job(self, name, command, extra, needs_apply, apply_changes, state, full):
        kwargs = dict(extra)
        if needs_apply and apply_changes:
            kwargs["apply"] = True

        if command == "import_openalex" and not full:
            since = state.get("last_run")
            if since:
                # Overlap by a day; OpenAlex backdates indexing.
                kwargs["since"] = (
                    timezone.datetime.fromisoformat(since) - timedelta(days=1)
                ).strftime("%Y-%m-%d")

        buffer = StringIO()
        started = time.monotonic()
        try:
            call_command(command, stdout=buffer, stderr=buffer, **kwargs)
            status = "ok"
            error = None
        except Exception as exc:  # a failed job must not abort the remaining ones
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"

        return {
            "command": command,
            "status": status,
            "error": error,
            "seconds": round(time.monotonic() - started, 1),
            "output": buffer.getvalue().strip().splitlines()[-12:],
        }

    def _audit(self):
        """Report conditions needing review. Never writes."""
        from academic.models import Faculty, NetworkInquiry, Paper
        from academic.views import _su_affiliated

        affiliated = Faculty.objects.filter(_su_affiliated())
        drift = [
            f.pk
            for f in Faculty.objects.annotate(linked=Count("papers", distinct=True))
            if (f.article_count or 0) != f.linked
        ]

        return {
            "faculty_total": Faculty.objects.count(),
            "faculty_su_affiliated": affiliated.count(),
            "faculty_external_coauthors": Faculty.objects.exclude(_su_affiliated()).count(),
            "metric_drift": len(drift),
            "pending_review": Faculty.objects.filter(review_status="pending").count(),
            "verified_missing_department": affiliated.filter(
                Q(department__isnull=True) | Q(department=""), directory_verified=True
            ).count(),
            "affiliated_missing_school": affiliated.filter(
                Q(school__isnull=True) | Q(school="")
            )
            .exclude(Q(department__isnull=True) | Q(department=""))
            .count(),
            "papers_total": Paper.objects.count(),
            "papers_without_authors": Paper.objects.annotate(n=Count("authors"))
            .filter(n=0)
            .count(),
            "papers_without_abstract": Paper.objects.filter(
                Q(abstract__isnull=True) | Q(abstract="")
            ).count(),
            "citations_total": Paper.objects.aggregate(n=Sum("tc_count"))["n"] or 0,
            "inquiries_unreviewed": NetworkInquiry.objects.filter(status="new").count(),
        }

    def _render(self, report):
        self.stdout.write(self.style.MIGRATE_HEADING(f"validation run ({report['mode']})"))

        for name, result in report["jobs"].items():
            style = self.style.SUCCESS if result["status"] == "ok" else self.style.ERROR
            self.stdout.write(style(f"  {name:<10} {result['status']}  {result['seconds']}s"))
            if result["error"]:
                self.stdout.write(self.style.ERROR(f"    {result['error']}"))
            for line in result["output"]:
                self.stdout.write(f"    | {line}")

        self.stdout.write(self.style.MIGRATE_HEADING("audit"))
        flagged = {"metric_drift", "pending_review", "inquiries_unreviewed"}
        for key, value in report["audits"].items():
            label = f"  {key:<32} {value}"
            self.stdout.write(self.style.WARNING(label) if key in flagged and value else label)

        self.stdout.write(f"\ncompleted in {report['duration_seconds']}s")
        if report["mode"] == "dry-run":
            self.stdout.write(self.style.WARNING("DRY RUN - re-run with --apply to write."))

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except json.JSONDecodeError:
                self.stderr.write(self.style.WARNING("state file unreadable; treating as first run"))
        return {}

    def _save_state(self, state):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
