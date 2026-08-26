"""
Management command to populate paper metadata (keywords, themes, categories) using Claude,
link papers to faculty, and aggregate faculty metrics.
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional

# Load environment variables FIRST before any Django imports
from dotenv import load_dotenv
load_dotenv()

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from anthropic import Anthropic
from anthropic.types import TextBlock, ThinkingBlock

from academic.models import Faculty, Paper, PaperAuthorship

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Populate paper metadata, link papers to faculty, and aggregate faculty metrics"

    def add_arguments(self, parser):
        parser.add_argument(
            "--categorize",
            action="store_true",
            help="Categorize papers with Claude (extract keywords, themes, categories)",
        )
        parser.add_argument(
            "--link",
            action="store_true",
            help="Link papers to faculty by author name matching",
        )
        parser.add_argument(
            "--aggregate",
            action="store_true",
            help="Aggregate faculty metrics from linked papers",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run all steps: categorize, link, and aggregate",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving to database",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=10,
            help="Number of papers per Claude batch (default: 10)",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=0,
            help="Maximum papers to process (0 = all)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        batch_size = options.get("batch", 10)
        max_papers = options.get("max", 0)

        # Default to --all if no specific option
        run_all = options.get("all", False)
        run_categorize = options.get("categorize", False) or run_all
        run_link = options.get("link", False) or run_all
        run_aggregate = options.get("aggregate", False) or run_all

        if not (run_categorize or run_link or run_aggregate):
            self.stdout.write(
                self.style.WARNING(
                    "No action specified. Use --categorize, --link, --aggregate, or --all"
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("=== RUNNING ALL STEPS ===\n"))

        if run_categorize:
            self.stdout.write("\n=== CATEGORIZING PAPERS ===")
            self.categorize_papers(dry_run, batch_size, max_papers)

        if run_link:
            self.stdout.write("\n=== LINKING PAPERS TO FACULTY ===")
            self.link_papers_to_faculty(dry_run)

        if run_aggregate:
            self.stdout.write("\n=== AGGREGATING FACULTY METRICS ===")
            self.aggregate_faculty_metrics(dry_run)

        self.stdout.write(self.style.SUCCESS("\n✓ All steps complete!\n"))

    def categorize_papers(self, dry_run: bool, batch_size: int, max_papers: int):
        """Use Claude to categorize papers lacking keywords/themes/categories."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise CommandError("ANTHROPIC_API_KEY not set. Add it to .env or export it.")

        client = Anthropic(api_key=api_key)

        # Find papers needing categorization
        papers_qs = Paper.objects.filter(
            keywords=[]
        )  # or can also check themes, categories
        if max_papers > 0:
            papers_qs = papers_qs[:max_papers]

        papers_list = list(papers_qs)
        total = len(papers_list)
        self.stdout.write(f"Found {total} papers needing categorization")

        if total == 0:
            return

        processed = 0
        for i in range(0, total, batch_size):
            batch_num = (i // batch_size) + 1
            papers_batch = papers_list[i : i + batch_size]
            self.stdout.write(
                f"Processing batch {batch_num} ({i + 1}-{min(i + batch_size, total)} of {total})..."
            )

            processed += self._process_paper_batch(client, papers_batch, dry_run)

        self.stdout.write(
            self.style.SUCCESS(f"✓ Categorization complete: {processed} papers")
        )

    def _process_paper_batch(self, client, papers_batch, dry_run: bool) -> int:
        """Send batch of papers to Claude for categorization."""
        papers_text = "\n\n".join(
            [
                f"Paper {i+1}:\nTitle: {p.title}\nAbstract: {p.abstract or 'N/A'}"
                for i, p in enumerate(papers_batch)
            ]
        )

        prompt = f"""Analyze these academic papers and extract structured metadata for each.

{papers_text}

For EACH paper, return ONLY valid JSON (no markdown, no thinking blocks) in this format:
[
  {{
    "paper_index": 1,
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "themes": ["theme1", "theme2"],
    "top_level_categories": ["category1"],
    "mid_level_categories": ["subcategory1"],
    "low_level_categories": ["detail1"]
  }}
]

Return ONLY the JSON array. No other text."""

        try:
            message = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract text from response, handling ThinkingBlock
            response_text = None
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text = block.text
                    break
                # Skip ThinkingBlock - it's just internal reasoning

            if not response_text:
                self.stdout.write(self.style.ERROR("  ✗ No text content in response"))
                return 0

            # Parse JSON
            try:
                results = json.loads(response_text)
            except json.JSONDecodeError as e:
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ Failed to parse Claude response as JSON: {e}"
                    )
                )
                return 0

            if not isinstance(results, list):
                self.stdout.write(
                    self.style.ERROR("  ✗ Expected JSON array from Claude")
                )
                return 0

            # Save results
            processed = 0
            for result in results:
                idx = result.get("paper_index", 1) - 1
                if idx < 0 or idx >= len(papers_batch):
                    continue

                paper = papers_batch[idx]
                paper.keywords = result.get("keywords", [])
                paper.themes = result.get("themes", [])
                paper.top_level_categories = result.get("top_level_categories", [])
                paper.mid_level_categories = result.get("mid_level_categories", [])
                paper.low_level_categories = result.get("low_level_categories", [])

                if not dry_run:
                    paper.save(
                        update_fields=[
                            "keywords",
                            "themes",
                            "top_level_categories",
                            "mid_level_categories",
                            "low_level_categories",
                        ]
                    )

                processed += 1

            self.stdout.write(f"  ✓ Processed {processed}/{len(papers_batch)} papers so far")
            return processed

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Error processing batch: {e}"))
            logger.error(f"Error processing paper batch: {e}")
            return 0

    def link_papers_to_faculty(self, dry_run: bool):
        """Link papers to faculty by matching author names."""
        # Build faculty name lookup
        faculty_names = {}
        for fac in Faculty.objects.all():
            name_lower = (fac.name or "").strip().lower()
            if name_lower:
                faculty_names.setdefault(name_lower, []).append(fac)

        self.stdout.write(f"Loaded {len(faculty_names)} faculty names for matching")

        # Find papers without author links
        papers_without_links = Paper.objects.filter(authors__isnull=True)
        total = papers_without_links.count()
        self.stdout.write(f"Found {total} papers without author links")

        linked = 0
        with transaction.atomic():
            for paper in papers_without_links:
                # Get author names from faculty_members field
                author_names = paper.faculty_members or []
                if not author_names:
                    continue

                for author_name in author_names:
                    author_lower = str(author_name).strip().lower()
                    if not author_lower:
                        continue

                    # Find matching faculty
                    matching_faculty = faculty_names.get(author_lower, [])
                    for fac in matching_faculty:
                        if not dry_run:
                            paper.authors.add(fac)
                            PaperAuthorship.objects.get_or_create(
                                paper=paper,
                                faculty=fac,
                                defaults={"status": "pending"},
                            )
                        linked += 1

        self.stdout.write(
            self.style.SUCCESS(f"✓ Linking complete: {linked} authorship records created")
        )

    def aggregate_faculty_metrics(self, dry_run: bool):
        """Calculate faculty metrics from linked papers."""
        faculty_list = Faculty.objects.all()
        updated = 0

        with transaction.atomic():
            for fac in faculty_list:
                papers = fac.papers.all()
                article_count = papers.count()
                total_citations = sum(p.tc_count or 0 for p in papers)
                average_citations = (
                    (total_citations / article_count) if article_count > 0 else 0.0
                )

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] {fac.name}: {article_count} articles, {total_citations} citations"
                    )
                else:
                    fac.article_count = article_count
                    fac.total_citations = total_citations
                    fac.average_citations = average_citations
                    fac.save(
                        update_fields=[
                            "article_count",
                            "total_citations",
                            "average_citations",
                        ]
                    )
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"✓ Aggregation complete: {updated} faculty records updated")
        )
