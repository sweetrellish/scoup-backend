import os
import json
import logging
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from dotenv import load_dotenv
import anthropic

from academic.models import Paper, Faculty

load_dotenv()
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Use Claude to categorize papers and link to faculty'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int, default=10, help='Papers per Claude batch')
        parser.add_argument('--max', type=int, default=0, help='Max papers to process (0=all)')
        parser.add_argument('--dry-run', action='store_true', help='Preview without saving')

    def handle(self, *args, **options):
        load_dotenv()
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise CommandError("ANTHROPIC_API_KEY not set in .env")

        client = anthropic.Anthropic(api_key=api_key)
        batch_size = options['batch']
        max_papers = options['max']
        dry_run = options['dry_run']

        self.stdout.write("\n=== ENRICHING PAPERS WITH CLAUDE ===\n")

        # Get papers that need enrichment (empty keywords)
        papers_qs = Paper.objects.exclude(keywords='[]').exclude(keywords='null')
        papers_without_keywords = Paper.objects.filter(keywords='[]') | Paper.objects.filter(keywords='null')
        
        if max_papers:
            papers_without_keywords = papers_without_keywords[:max_papers]
        
        papers = list(papers_without_keywords)
        self.stdout.write(f"Found {len(papers)} papers needing enrichment")

        if not papers:
            self.stdout.write(self.style.SUCCESS("✓ All papers already enriched"))
            return

        processed = 0
        for i in range(0, len(papers), batch_size):
            batch = papers[i:i+batch_size]
            batch_num = i // batch_size + 1
            self.stdout.write(f"\nBatch {batch_num} ({i+1}-{min(i+batch_size, len(papers))} of {len(papers)})")
            
            # Prepare batch data for Claude
            papers_text = ""
            for paper in batch:
                papers_text += f"\n[PAPER {paper.doi}]\nTitle: {paper.title}\nAbstract: {paper.abstract[:500] if paper.abstract else 'N/A'}\n"

            prompt = f"""Analyze these academic papers and extract metadata.

{papers_text}

For each paper identified by DOI, return JSON with:
- doi: the DOI from [PAPER xxx]
- keywords: list of 5-10 relevant keywords
- themes: list of 2-3 research themes
- top_level_categories: broad research area
- ai_keywords: AI/ML related keywords if applicable, empty list otherwise

Return ONLY valid JSON array (no markdown):
[
  {{
    "doi": "10.xxx",
    "keywords": ["keyword1", "keyword2"],
    "themes": ["theme1"],
    "top_level_categories": ["Computer Science"],
    "ai_keywords": []
  }}
]"""

            try:
                message = client.messages.create(
                    model="claude-opus-5",
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}]
                )

                # Extract text response
                response_text = None
                for block in message.content:
                    if hasattr(block, 'text'):
                        response_text = block.text
                        break
                
                if not response_text:
                    self.stdout.write(self.style.WARNING("  ✗ No text response from Claude"))
                    continue

                # Parse JSON
                response_text = response_text.strip()
                if response_text.startswith("```json"):
                    response_text = response_text[7:].strip()
                if response_text.startswith("```"):
                    response_text = response_text[3:].strip()
                if response_text.endswith("```"):
                    response_text = response_text[:-3].strip()

                results = json.loads(response_text)

                # Save to database
                if not dry_run:
                    with transaction.atomic():
                        for result in results:
                            doi = result.get('doi')
                            paper = next((p for p in batch if p.doi == doi), None)
                            if not paper:
                                continue

                            paper.keywords = result.get('keywords', [])
                            paper.themes = result.get('themes', [])
                            paper.top_level_categories = result.get('top_level_categories', [])
                            paper.ai_keywords = result.get('ai_keywords', [])
                            paper.save()

                    self.stdout.write(self.style.SUCCESS(f"  ✓ Processed {len(results)} papers"))
                    processed += len(results)
                else:
                    self.stdout.write(f"  [DRY RUN] Would process {len(results)} papers")
                    processed += len(results)

            except json.JSONDecodeError as e:
                self.stdout.write(self.style.WARNING(f"  ✗ JSON parse error: {str(e)[:60]}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Error: {str(e)[:60]}"))
                logger.error(f"Error in batch {batch_num}: {e}")

        self.stdout.write(self.style.SUCCESS(f"\n✓ Enrichment complete: {processed} papers processed\n"))
