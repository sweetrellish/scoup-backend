import os
import json
import logging
import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from dotenv import load_dotenv
import anthropic

from academic.models import Paper, Faculty

load_dotenv()
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Use Claude to verify faculty data from publications'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int, default=5)
        parser.add_argument('--max', type=int, default=0)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        load_dotenv()
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise CommandError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        batch_size = options['batch']
        max_papers = options['max']
        dry_run = options['dry_run']

        self.stdout.write("\n=== VERIFYING FACULTY FROM PUBLICATIONS ===\n")

        all_papers = list(Paper.objects.all().order_by('id'))
        if max_papers:
            all_papers = all_papers[:max_papers]

        self.stdout.write(f"Processing {len(all_papers)} papers to verify faculty\n")

        processed = 0
        faculty_updated = 0
        
        for i in range(0, len(all_papers), batch_size):
            batch = all_papers[i:i+batch_size]
            batch_num = i // batch_size + 1
            self.stdout.write(f"Batch {batch_num} ({i+1}-{min(i+batch_size, len(all_papers))} of {len(all_papers)})")
            
            papers_text = ""
            for paper in batch:
                title = paper.title.replace('"', "'").replace('\n', ' ')[:200]
                abstract = (paper.abstract or '').replace('"', "'").replace('\n', ' ')[:200]
                keywords = ', '.join(str(k).replace('"', "'") for k in paper.keywords[:3]) if paper.keywords else 'N/A'
                
                papers_text += f"\n[PAPER {paper.doi}]\nTitle: {title}\nAbstract: {abstract}\nKeywords: {keywords}\n"

            prompt = f"""For each academic paper, extract authors likely from Salisbury University.

{papers_text}

Return ONLY valid JSON array:
[
  {{"doi": "10.xxx", "salisbury_authors": ["Author Name"], "departments": ["Department"]}}
]"""

            try:
                message = client.messages.create(
                    model="claude-opus-5",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}]
                )

                # Extract text from response (skip thinking blocks)
                response_text = None
                for block in message.content:
                    # Skip thinking blocks, get text block
                    if hasattr(block, '__class__') and 'TextBlock' in block.__class__.__name__:
                        response_text = block.text
                        break
                    elif hasattr(block, 'text') and not hasattr(block, 'thinking'):
                        response_text = block.text
                        break
                
                if not response_text:
                    self.stdout.write(self.style.WARNING("  ✗ No text in response"))
                    continue

                response_text = response_text.strip()
                
                # Remove markdown
                for prefix in ["```json", "```"]:
                    if response_text.startswith(prefix):
                        response_text = response_text[len(prefix):].strip()
                if response_text.endswith("```"):
                    response_text = response_text[:-3].strip()
                
                # Extract JSON array
                match = re.search(r'\[\s*\{.*?\}\s*\]', response_text, re.DOTALL)
                if match:
                    json_str = match.group(0)
                else:
                    json_str = response_text
                
                results = json.loads(json_str)
                if not isinstance(results, list):
                    results = [results]

                if not dry_run:
                    with transaction.atomic():
                        for result in results:
                            if not isinstance(result, dict):
                                continue
                            
                            doi = result.get('doi')
                            paper = next((p for p in batch if p.doi == doi), None)
                            if not paper:
                                continue

                            paper.faculty_members = result.get('salisbury_authors', [])
                            paper.save()

                            for author_name in result.get('salisbury_authors', []):
                                if not author_name or len(author_name) < 2:
                                    continue
                                    
                                fac = Faculty.objects.filter(name__icontains=author_name).first()
                                if fac:
                                    depts = result.get('departments', [])
                                    if depts and not fac.department:
                                        fac.department = depts[0]
                                    fac.save()
                                    faculty_updated += 1

                    self.stdout.write(self.style.SUCCESS(f"  ✓ Processed {len(results)} papers"))
                    processed += len(results)
                else:
                    self.stdout.write(f"  [DRY RUN] Would verify {len(results)} papers")
                    processed += len(results)

            except json.JSONDecodeError as e:
                self.stdout.write(self.style.WARNING(f"  ✗ JSON: {str(e)[:40]}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  ✗ Error: {str(e)[:60]}"))

        self.stdout.write(self.style.SUCCESS(f"\n✓ Complete: {processed} papers, {faculty_updated} faculty\n"))
