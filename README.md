# SCOUP Backend

This repository contains the Django backend for the SCOUP research-discovery platform. It provides the public data feed, faculty and paper review logic, admin approval workflows, and the server-side trust boundaries that determine what is public vs what remains in an internal review queue.

This README is intentionally supplemental to the older project notes. It reflects the current architecture and product boundaries without exposing deployment details.

---

## 1. Current Product Scope

The backend powers:

- Public search endpoints for faculty, papers, projects, and patents
- Faculty public profiles
- Category / expertise-map data
- Admin approval queues for faculty and papers
- Collaboration inquiry and support ticket APIs
- Search relevance scoring and data-quality filtering
- Reviewable “pending” queues for ambiguous or unverified records

The most important concept in the current product is that public visibility is not based on raw dataset size; it is based on trust and review status.

---

## 2. Core Architecture

### Main app

- `academic/`

### Key files

- `academic/models.py` — all core data models
- `academic/views.py` — public search, category logic, relevance scoring, dataset assembly
- `academic/admin_views.py` — admin dashboard stats and faculty review endpoints
- `academic/public_profile_views.py` — public faculty profile API
- `academic/network_views.py` — network discovery and prominence logic
- `academic/admin_paper_views.py` — personal admin review queue for restored pending papers
- `academic/urls.py` — public/admin API routes
- `academic/management/commands/` — data validation, import, purge, and repair scripts

---

## 3. Trust & Review Model

The current backend treats the database as two layers:

1. Approved public data
2. Pending / rejected administrative review data

Only records with `review_status='approved'` are exposed in public search results.

This pattern is used for both faculty and papers.

### Why this matters

This prevents uncertain or unverifiable records from going public just because they appear in an upstream source. It keeps the website usable and trustworthy while preserving ambiguous records for manual review.

---

## 4. Search Relevance Model

The search model in `academic/views.py` is intentionally conservative.

### Core logic

- Query terms are normalized and tokenized.
- Matches must happen on word boundaries.
- Multi-word queries require all terms to match somewhere.
- Title matches are weighted highest.
- Abstract and theme matches are usable, but lower confidence than title hits.
- Keyword matches are discounted when the keyword list is noisy, long, or too common.

### Key safeguards

- `_keyword_trust()` reduces confidence for long or scattered keyword lists.
- `_keyword_specificity()` applies a document-frequency-based discount for common umbrella terms.
- `_MIN_CONFIDENCE` prevents weak matches from being surfaced as results.
- Broad tags like highly common science umbrella labels are treated as weak evidence, not ground truth.

This logic was added after observed false positives where unrelated papers were ranking as near-perfect matches because large upstream keyword sets included broad but irrelevant tags.

---

## 5. Public Data Pipeline

The public dataset is assembled in `public_search_data()`.

That endpoint returns:

- `facultyData`
- `papersData`
- `patentsData`
- `projectsData`

Important fields included in the public payload:

- `profileId` for frontend faculty profile linking
- `datePrecise` on paper records so date charts do not fake trend data from Jan 1 placeholder dates
- approved-only record filtering

This keeps the frontend simple while preserving strong backend safety rules.

---

## 6. Admin Review Queues

### Faculty queue

`admin_views.py` manages faculty review flows.

### Paper queue

`admin_paper_views.py` handles the restored pending-paper review queue.

This queue is intentionally separate from the public dataset and is restricted to superusers, not generic staff accounts.

Current endpoints:

- `GET /api/admin/papers/`
- `POST /api/admin/papers/<id>/approve/`
- `POST /api/admin/papers/<id>/reject/`
- `POST /api/admin/papers/bulk-action/`

This allows manual review of ambiguous papers without permanently deleting them.

---

## 7. Data Integrity & Cleanup Commands

The backend includes a number of repair and validation commands in `academic/management/commands/`.

Notable examples:

- `verify_paper_institutions.py` — verifies institution tags against reliable signals
- `crossref_su_directory.py` — cross-checks candidates against the real SU directory
- `sync_paper_author_links.py` — restores author-paper link integrity
- `restore_purged_papers.py` — restores purged records as pending rather than deleting them permanently
- `purge_pre_founding_papers.py` — removes records before the university founding year when clearly invalid
- `purge_unverified_papers.py` — removes records with insufficient verification evidence

These commands are not cosmetic; they are part of the trust layer that keeps the public dataset defensible.

---

## 8. Important Operational Rules

These are important for anyone maintaining or presenting the system:

- Never import or source an unaudited `.env` file into a shell without checking it.
- Use explicit permission checks rather than relying on a general staff role.
- Keep public search filtered to `review_status='approved'` only.
- Treat broad, upstream metadata as weak evidence unless it is corroborated.
- Back up the database before any data repair or purge operation.

---

## 9. Reference Documents

This backend includes the current project notes and architecture references:

- `docs/documentation-index.md` — entry point for the current documentation set
- `docs/website-architecture-playbook.md` — full architecture and confidence model reference
- `docs/semantic-search-notes.md` — early search design notes and future concept work

---

## 10. Local Development

Typical backend workflow:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Check health and routing with:

```bash
python manage.py check
```

---

## 11. Backend Presentation Summary

If you are presenting the backend, the strongest narrative is:

- The platform is not just a search index; it is a research-discovery product with a human trust layer.
- Search quality was improved by reducing noise from generic tags and broad concept labels.
- The system deliberately keeps ambiguous records out of public view until they are reviewed.
- The admin review queue and integrity scripts are part of the platform's product trust model, not afterthought cleanup.

---

The backend is now intentionally structured to support both public discovery and private editorial review in the same system, while keeping the public-facing experience legitimately defensible.

---

## Supplemental Current-State Documentation

This repository also contains additional current-state references that explain the live architecture and product logic:

- `docs/documentation-index.md` — index for the current documentation set
- `docs/website-architecture-playbook.md` — full architecture and confidence-model guide
- `docs/semantic-search-notes.md` — earlier search design notes and planned future semantic work

The live backend is no longer limited to the original initial app structure. It now contains a trust-boundary layer for reviewable records and a stricter search relevance model that actively protects the public dataset from noisy or over-broad metadata.
