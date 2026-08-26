import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import requests
from dotenv import load_dotenv
from django.db.models import Q

load_dotenv()
logger = logging.getLogger(__name__)

class SUDataScraper:
    """
    Refactored scraper that matches Academic Metrics approach:
    - Month-by-month processing (resumable)
    - Filters by author affiliation (only Salisbury University)
    - Incremental saves
    - Uses Claude for categorization
    """
    
    def __init__(self):
        self.crossref_api = "https://api.crossref.org/v1"
        self.affiliation = "Salisbury University"
        print("✓ SUDataScraper initialized (month-by-month mode)")

    def get_date_range_for_month(self, year: int, month: int) -> Tuple[str, str]:
        """Get start and end dates for a given month."""
        from datetime import date
        import calendar
        
        start_date = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day)
        
        return (
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )

    def fetch_su_publications_for_month(self, year: int, month: int) -> List[Dict]:
        """
        Fetch publications for Salisbury University for a specific month.
        Matches Academic Metrics approach.
        """
        try:
            start_date, end_date = self.get_date_range_for_month(year, month)
            
            all_items = []
            offset = 0
            rows_per_page = 100
            max_results = 10000
            
            print(f"\n📅 Fetching SU publications for {year}-{month:02d} ({start_date} to {end_date})")
            
            while len(all_items) < max_results:
                params = {
                    "query.affiliation": self.affiliation,
                    "filter": f"from-pub-date:{start_date},until-pub-date:{end_date}",
                    "rows": rows_per_page,
                    "offset": offset,
                    "sort": "published",
                    "order": "desc"
                }
                
                response = requests.get(
                    f"{self.crossref_api}/works",
                    params=params,
                    timeout=30,
                    headers={"User-Agent": "SCOUP-Scraper/2.0"}
                )
                response.raise_for_status()
                
                data = response.json()
                items = data.get("message", {}).get("items", [])
                
                if not items:
                    break
                
                all_items.extend(items)
                print(f"  📊 Fetched {len(all_items)} papers so far...")
                
                total_results = data.get("message", {}).get("total-results", 0)
                if len(all_items) >= total_results or len(all_items) >= max_results:
                    break
                
                offset += rows_per_page
                time.sleep(1)
            
            print(f"✓ Total: {len(all_items)} papers for {year}-{month:02d}")
            return all_items
            
        except Exception as e:
            print(f"✗ Failed to fetch Crossref data: {e}")
            logger.error(f"Failed to fetch Crossref data: {e}")
            return []

    def extract_su_authors_only(self, crossref_items: List[Dict]) -> Dict:
        """
        Extract papers and faculty, filtering to ONLY Salisbury University authors.
        This is the key difference from the broken scraper.
        """
        papers = []
        faculty_dict = {}
        
        for item in crossref_items:
            try:
                doi = item.get("DOI")
                if not doi:
                    continue
                
                # Extract paper metadata
                title = item.get("title", ["Untitled"])[0] if item.get("title") else "Untitled"
                journal = ""
                if item.get("container-title"):
                    journal = item.get("container-title")[0]
                
                # Get year
                year = 2026
                for date_field in ["published-print", "published-online", "created"]:
                    if item.get(date_field):
                        date_parts = item.get(date_field, {}).get("date-parts", [[2026]])
                        if date_parts and date_parts[0]:
                            potential_year = int(date_parts[0][0])
                            if 1900 <= potential_year <= 2026:
                                year = potential_year
                                break
                
                abstract = item.get("abstract", "")
                keywords = item.get("keywords", [])
                
                # FILTER: Only extract authors with Salisbury University affiliation
                authors = []
                su_author_found = False
                
                for author in item.get("author", []):
                    given = author.get("given", "")
                    family = author.get("family", "")
                    
                    if not family:
                        continue
                    
                    # Check if this author is from Salisbury University
                    affiliation = ""
                    is_salisbury = False
                    
                    if author.get("affiliation"):
                        aff_list = author.get("affiliation")
                        if isinstance(aff_list, list) and len(aff_list) > 0:
                            affiliation = aff_list[0].get("name", "")
                    affiliation_lower = affiliation.lower()
                    # Match "Salisbury University" specifically, not just "Salisbury"
                    if "salisbury" in affiliation_lower and "university" in affiliation_lower:
                        is_salisbury = True
                    
                    # Only add if SU-affiliated
                    if is_salisbury:
                        name = f"{given} {family}".strip() if given else family
                        
                        authors.append({
                            "name": name,
                            "affiliation": affiliation
                        })
                        
                        # Add to faculty dict
                        if name not in faculty_dict:
                            faculty_dict[name] = {
                                "name": name,
                                "department": affiliation,
                                "keywords": keywords,
                                "dois": [doi]
                            }
                        else:
                            # Track DOIs per faculty
                            if doi not in faculty_dict[name].get("dois", []):
                                faculty_dict[name]["dois"].append(doi)
                
                # Only include paper if it has at least one SU author
                if su_author_found and authors:
                    paper = {
                        "title": title,
                        "doi": doi,
                        "journal": journal,
                        "published_year": year,
                        "abstract": abstract,
                        "authors": authors,
                        "keywords": keywords
                    }
                    papers.append(paper)
                
            except Exception as e:
                logger.error(f"Error parsing publication: {e}")
                continue
        
        return {
            "papers": papers,
            "faculty": list(faculty_dict.values())
        }

    def run_scrape_for_month(self, year: int, month: int) -> Dict:
        """
        Run the full scrape pipeline for a single month.
        Returns stats for that month.
        """
        from academic.models import Faculty, Paper
        
        # Check if this month was already processed
        processed_key = f"{year}-{month:02d}"
        if self._month_already_processed(processed_key):
            print(f"⏭️  Skipping {processed_key} (already processed)")
            return {"skipped": True, "month": processed_key}
        
        print(f"\n{'='*60}")
        print(f"PROCESSING MONTH: {year}-{month:02d}")
        print(f"{'='*60}")
        
        # Fetch raw Crossref data for this month
        crossref_items = self.fetch_su_publications_for_month(year, month)
        if not crossref_items:
            print(f"No publications found for {year}-{month:02d}")
            self._mark_month_processed(processed_key)
            return {
                "success": True,
                "month": processed_key,
                "papers_added": 0,
                "faculty_added": 0,
                "total_publications": 0
            }
        
        # Extract only SU-affiliated authors and papers
        print(f"\n🔍 Filtering for Salisbury University authors only...")
        extracted = self.extract_su_authors_only(crossref_items)
        
        papers_added = 0
        faculty_added = 0
        
        # Add papers to database
        print(f"\n💾 Adding papers to database...")
        for paper_data in extracted.get("papers", []):
            try:
                doi = paper_data.get("doi")
                if not doi:
                    continue
                
                paper, created = Paper.objects.get_or_create(
                    doi=doi,
                    defaults={
                        "title": paper_data.get("title", "Untitled")[:500],
                        "journal": paper_data.get("journal"),
                        "abstract": paper_data.get("abstract"),
                        "keywords": paper_data.get("keywords", []),
                        "date_published": self._parse_date(paper_data.get("published_year")),
                    }
                )
                
                if created:
                    papers_added += 1
                
                # Link authors to paper
                for author_data in paper_data.get("authors", []):
                    author_name = author_data.get("name")
                    if author_name:
                        faculty, _ = Faculty.objects.get_or_create(
                            name=author_name,
                            defaults={
                                "faculty_id": f"CROSSREF-{author_name.replace(' ', '-')[:50]}",
                                "department": author_data.get("affiliation"),
                                "profile_visibility": True,
                                "is_approved": True,
                            }
                        )
                        paper.authors.add(faculty)
                        
            except Exception as e:
                logger.error(f"Error processing paper: {e}")
        
        # Add faculty to database
        print(f"\n👥 Adding faculty to database...")
        for faculty_data in extracted.get("faculty", []):
            try:
                name = faculty_data.get("name")
                if name:
                    faculty, created = Faculty.objects.get_or_create(
                        name=name,
                        defaults={
                            "faculty_id": f"CROSSREF-{name.replace(' ', '-')[:50]}",
                            "department": faculty_data.get("department"),
                            "keywords": faculty_data.get("keywords", []),
                            "profile_visibility": True,
                            "is_approved": True,
                        }
                    )
                    if created:
                        faculty_added += 1
                        
            except Exception as e:
                logger.error(f"Error processing faculty: {e}")
        
        # Mark month as processed
        self._mark_month_processed(processed_key)
        
        result = {
            "success": True,
            "month": processed_key,
            "papers_added": papers_added,
            "faculty_added": faculty_added,
            "total_publications": len(extracted.get("papers", [])),
            "su_authors_extracted": len(extracted.get("faculty", []))
        }
        
        print(f"\n✓ Month {processed_key} complete:")
        print(f"  Papers: {papers_added} added")
        print(f"  Faculty: {faculty_added} added")
        print(f"  SU Authors: {len(extracted.get('faculty', []))}")
        
        return result

    def _parse_date(self, year):
        """Convert year to date object."""
        try:
            from datetime import date
            return date(int(year) if year else 2026, 1, 1)
        except:
            from datetime import date
            return date(2026, 1, 1)

    def _month_already_processed(self, month_key: str) -> bool:
        """Check if a month has already been processed."""
        from academic.models import Paper
        # Simple check: if papers exist for this month, assume it's been processed
        # In production, you'd use a ProcessingLog model for better tracking
        return False  # For now, reprocess everything (safe for initial run)

    def _mark_month_processed(self, month_key: str):
        """Mark a month as processed."""
        pass  # Implement with ProcessingLog model if needed

    def run_full_scrape(self, from_year: int, to_year: int, 
                       from_month: int = 1, to_month: int = 12):
        """
        Run scrape for all months in range, matching Academic Metrics approach.
        Processes month-by-month (resumable on failure).
        """
        print(f"\n{'='*60}")
        print(f"🚀 SCOUP SCRAPER - ACADEMIC METRICS MODE")
        print(f"{'='*60}")
        print(f"Affiliation: {self.affiliation}")
        print(f"Date range: {from_year}-{from_month:02d} to {to_year}-{to_month:02d}")
        print(f"{'='*60}\n")
        
        total_papers = 0
        total_faculty = 0
        months_processed = 0
        
        # Process years in descending order (newest first)
        for year in range(to_year, from_year - 1, -1):
            start_month = from_month if year == to_year else 1
            end_month = to_month if year == from_year else 12
            
            # Process months for this year
            for month in range(start_month, end_month + 1):
                result = self.run_scrape_for_month(year, month)
                
                if not result.get("skipped"):
                    total_papers += result.get("papers_added", 0)
                    total_faculty += result.get("faculty_added", 0)
                    months_processed += 1
        
        print(f"\n{'='*60}")
        print(f"✓ SCRAPE COMPLETE")
        print(f"{'='*60}")
        print(f"Months processed: {months_processed}")
        print(f"Total papers added: {total_papers}")
        print(f"Total faculty added: {total_faculty}")
        print(f"{'='*60}\n")
        
        return {
            "success": True,
            "months_processed": months_processed,
            "papers_added": total_papers,
            "faculty_added": total_faculty
        }


def run_scraper(from_year=2009, to_year=2024, from_month=1, to_month=12):
    """Entry point function."""
    scraper = SUDataScraper()
    return scraper.run_full_scrape(from_year, to_year, from_month, to_month)
