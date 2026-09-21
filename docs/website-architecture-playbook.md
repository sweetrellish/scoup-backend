# SCOUP Website Architecture & Search Playbook

This document is the operating manual for the current SCOUP implementation. It captures the architecture, feature logic, search confidence model, data-quality safeguards, and the dependency map between the Django backend, the React frontend, and the worker-built features that now sit in production.

Status: current as of 2026-08-30.

---

## 1. Product Purpose

SCOUP is a Salisbury University research discovery platform for public browsing, faculty profiling, collaboration discovery, and administrative review. The current live product contains:

- Public search for faculty, papers, projects, and patents
- Faculty profile pages and research discovery pages
- Expertise map / category discovery
- Admin review queues for faculty and restored papers
- Public inquiry and support ticket flows
- Validation and purge logic to keep low-confidence institutional data out of public search

The design goal is to make the website useful to external visitors without exposing low-confidence or unverifiable institutional records.

---

## 2. System Architecture

### 2.1 Backend

Framework:

- Django 5.2.7
- Django REST Framework 3.16.1
- SQLite for local/dev and production here
- Gunicorn + Nginx in deployment

Primary backend app:

- `academic/`

Key modules:

- `academic/models.py` — core data models
- `academic/views.py` — public search, category indexing, primary search logic
- `academic/network_views.py` — network discovery and prominence logic
- `academic/admin_views.py` — admin dashboard metrics and faculty approval flow
- `academic/admin_paper_views.py` — admin queue for restored pending papers
- `academic/public_profile_views.py` — public faculty profile endpoints
- `academic/urls.py` — route definitions
- `academic/management/commands/` — data repair / validation / import / purge scripts

### 2.2 Frontend

Framework:

- React + TypeScript + Vite
- Public dataset loaded from backend JSON endpoints
- Search UI components built around `publicData.ts` and `searchEngine.ts`

Core frontend files:

- `src/App.tsx` — route shell and auth gating
- `src/components/Home.tsx` — home search and analytics
- `src/components/SearchResults.tsx` — result rendering and inquiry flow
- `src/utils/publicData.ts` — public dataset fetch + confidence scoring helper
- `src/utils/searchEngine.ts` — suggestion engine, faculty matching, fuzzy match logic
- `src/components/admin/PendingApprovalsPage.tsx` — faculty approval queue UI
- `src/components/CapabilitiesPage.tsx` — expertise map / bubble layout
- `src/components/FacultyProfilePage.tsx` — public faculty profile UI
- `src/components/Documentation.tsx` — docs and repo links

### 2.3 Runtime Dependency Map

Public website flow:

1. Backend `/api/public/search-data/` emits the public dataset payload.
2. Frontend `fetchPublicDataset()` loads that dataset.
3. `setSearchDataset()` indexes faculty, papers, projects, and patents in memory.
4. User query triggers `performSearch()` in the frontend search engine and/or backend search endpoint.
5. Result cards render and inquiry flows route messages into admin/inquiry systems.

Admin flow:

1. Admin login authenticates against Django auth.
2. `admin_views.py` exposes list/approve/reject endpoints for faculty.
3. `admin_paper_views.py` exposes matching endpoints for pending papers.
4. Admin dashboard pages call these endpoints and render queue data.

---

## 3. Data Model Overview

### 3.1 Faculty

Core fields in `academic/models.py` include:

- `faculty_id`, `orcid`, `openalex_id`
- `first_name`, `last_name`, `title`, `department`, `school`, `email`
- `bio`, `ai_keywords`, `faculty_keywords`, `keywords`
- `profile_visibility`, `directory_verified`, `is_approved`
- `review_status`, `review_note`
- `photo`, `created_at`, `updated_at`

Important concept:

- Faculty can exist in a pending/approved/rejected review flow.
- `directory_verified` is a strong trust signal, but it was not treated as sufficient on its own for public records.
- `profile_visibility` controls public exposure independently of approval.

### 3.2 Papers

Paper fields include:

- `doi`, title, abstract, journal, dates
- `keywords`, `themes`, `top_level_categories`, `mid_level_categories`, `low_level_categories`
- `authors`, `faculty_members`, `faculty_affiliations`
- `tc_count` (citation count)
- `source_metadata`, `engagement_metrics`, `source_record`
- `review_status`, `review_note`

Important concept:

- Public endpoints only return papers where `review_status='approved'`.
- Rejected or pending papers are kept hidden from public search by the backend.

### 3.3 Supporting models

- `Project` — public project results and collaboration-interest workflows
- `Patent` — public patent records
- `PaperAuthorship` — author-link verification state
- `FacultySuggestionDecision` — external faculty review decisions
- `NetworkInquiry` — public collaboration submissions

---

## 4. Search System: How It Works

## 4.1 Public dataset generation

The public dataset is built by `academic/views.py` in `public_search_data()`.

This endpoint:

- loads visible faculty records only
- loads approved papers only
- loads projects and patents
- serializes them into the frontend-friendly objects used by the search UI

Important behavior:

- Faculty records include `profileId`, a numeric ID used by the profile route
- Papers include a `datePrecise` flag used to prevent Jan 1 placeholder dates from faking trend charts

Example of the data flow:

`public_search_data()`
  -> `Paper.objects.filter(review_status="approved")`
  -> serialize `facultyData`, `papersData`, `patentsData`, `projectsData`
  -> frontend `fetchPublicDataset()`
  -> `setSearchDataset()` indexes the data

### 4.2 Query-time relevance in the backend

The main paper relevance logic lives in `academic/views.py`:

- `_tokenize_query()`
- `_word_match()`
- `_clean_abstract()`
- `_keyword_trust()`
- `_keyword_specificity()`
- `_score_paper()`
- `_MIN_CONFIDENCE`

The scoring logic is intentionally stricter than a naive keyword match because the data was noisy.

### 4.3 Why the original confidence drifted

The `keywords` field from upstream metadata was often broad and overused. A paper could carry a large list of generic tags such as "Computer science" or "Political science" and accidentally rank highly for unrelated queries. That led to false parametric matches, including a case where a reading-comprehension paper scored extremely highly on a query like "computer science" because a broad field label was present among many unrelated tags.

The fix was to treat the keyword field as weaker and more specific evidence than title and abstract matches.

### 4.4 Field weights and specificity

The backend uses:

- Title: 5.0
- Themes: 3.0
- Abstract: 2.5
- Keywords: 2.0
- Journal: 1.0

The real fix was not just a weighting change. The algorithm also evaluates how common a keyword is across the corpus.

- `_keyword_trust(keyword_list)` reduces the trust of a keyword list as it grows larger and more scattered.
- `_keyword_specificity(keyword)` discounts common umbrella terms using document frequency.
- `_KEYWORD_COMMON_THRESHOLD = 150` makes broad tags become weak evidence rather than decisive evidence.

This is the backend equivalent of a TF-IDF approach for search relevance.

### 4.5 The “every term must match” rule

One crucial logic rule prevents weak partial matches from passing:

- If a query has multiple terms, and not every term is found in the matching paper, the result is rejected.
- This stops things like matching only "computer" or only "science" and then declaring the whole phrase relevant.

This is implemented with coverage checks and the real-match threshold.

### 4.6 Exact phrase and keyword bonuses

A match receives extra confidence when:

- the phrase matches the title exactly
- the phrase matches a theme or abstract segment
- the exact keyword is present in the paper keyword list

These bonuses are still constrained by keyword trust and specificity so broad, high-frequency tags cannot dominate the ranking.

### 4.7 Confidence output

The score is then normalized to a range up to 100 and returned as a confidence number. The backend rejects any candidate below `_MIN_CONFIDENCE = 30.0`.

This threshold is intentionally conservative to avoid noisy matches.

### 4.8 Frontend search pipeline

The frontend maintains its own search experience in:

- `src/utils/publicData.ts`
- `src/utils/searchEngine.ts`

Public search is loaded as a unified dataset, then indexed in memory.

The frontend supports:

- type-specific filtering (Faculty / Papers / Patents / Projects)
- suggestions/autocomplete
- “Did you mean?” fallback suggestions
- faculty matching directly against in-memory data when backend search is not enough for faculty cards

This is a deliberate hybrid: the backend handles authoritative paper ranking, while the frontend handles the UI and in-memory faculty matching experience.

---

## 5. Search Confidence Model Summary

The practical scoring rules are:

1. Exact title phrase match = extremely strong signal
2. All query terms present = required for multi-word queries
3. Title matches outrank abstract matches
4. Keyword matches are discounted when the keywords are too common or too broad
5. Long keyword lists are treated as noisy rather than authoritative
6. Broad umbrella categories are penalized by document frequency
7. Generic tag-only hits get down-weighted heavily
8. Papers with low real support are rejected before ranking

This is what made the search much more trustworthy than the earlier pure-keyword or broad-match approach.

---

## 6. Faculty Review Queue and Admin Safety Model

### 6.1 Faculty approval queue

`academic/admin_views.py` includes a protected faculty admin interface that lists all faculty records with `review_status='pending'` and allows approve/reject actions.

The review flow exists because identity matching must not be forced into automatic approval if the directory match is ambiguous.

### 6.2 Paper review queue

The restored purge queue is implemented in:

- `academic/admin_paper_views.py`
- `academic/urls.py`
- `academic/models.py`

Key semantics:

- `Paper.review_status` is one of `approved`, `pending`, or `rejected`
- The public endpoints filter to approved papers only
- Pending papers are hidden from public search but remain available to an admin review queue
- Rejecting a paper stores a human-written note and keeps it hidden

This queue exists for the 2026-08-30 purge restoration so the user can review ambiguous papers manually without silently exposing them or silently discarding them.

### 6.3 Protection rule for the personal review queue

The review queue endpoints are restricted to superusers, not generic staff, to keep the personal review bucket under `rellis` control only.

This is enforced directly in `admin_paper_views.py` by a custom `IsSuperUser` permission class.

---

## 7. Data Purge and Validation Logic

A major part of the current architecture is not just feature development, but data integrity governance.

### 7.1 Validation and decision logic

Files in `academic/management/commands/` include:

- `verify_paper_institutions.py`
- `crossref_su_directory.py`
- `sync_paper_author_links.py`
- `restore_purged_papers.py`
- `purge_pre_founding_papers.py`
- `purge_unverified_papers.py`

These were created to answer one core business question:

> Which records are genuine Salisbury University publications and which ones are only weakly related or institutionally unsupported?

### 7.2 Why the directory match logic was tightened

The original directory matching used broader heuristics and could false-match names with the same surname and first initial. That was removed because it caused false positives and untrustworthy approvals.

The final logic only kept robust exact matches and narrow formatting normalization, avoiding accidental matches that looked plausible but were not real.

### 7.3 Why the restore queue exists

A paper was not moved to public search simply because it was in the old database or because a weak OpenAlex tag suggested it belonged. Instead, the implementation uses a human review bucket so the user can inspect each ambiguous case before deciding if it belongs in the live dataset.

---

## 8. Frontend Feature Map

### 8.1 Public site

Key public pages in the current frontend include:

- `/` — home search
- `/search` — dedicated search view
- `/browse` — category index
- `/expertise-map` — expertise clustering
- `/about`, `/contact`, `/docs`
- `/faculty/<id>` — public faculty profile page

### 8.2 Admin pages

The admin dashboard includes approval queues and admin-only analytics.

The faculty approval queue in `PendingApprovalsPage.tsx` is the pattern the paper review page mirrors.

### 8.3 Search UI behavior

The home page does all of the following:

- loads public dataset on mount
- indexes the dataset for suggestions and term matching
- allows multiple result-type filters
- toggles and reorganizes result pools
- distinguishes placeholder-dated papers from precise publication dates

This is the user-facing layer that makes all the backend data trust decisions visible.

---

## 9. Important Implementation Notes

### 9.1 `review_status` as the trust boundary

`review_status` is the central safety feature that decided whether a record is accessible to the public. That rule was applied to:

- faculty
- papers
- paper accessibility in public search and category flows

This is the architecture-level boundary that prevents unverified content from leaking into the public product.

### 9.2 Immutable public search rule

Every public-facing paper query was filtered to `approved` only. That includes the dataset used by the frontend search and the category-based paper sets.

This matters because it keeps the review queue and public product separate even when the underlying database still contains the larger reviewable set.

### 9.3 Do-not-source-env rule

Two lessons mattered deeply in this implementation:

- never `source` an unaudited `.env` file into a shell
- never assume a frontend variable from the backend env is harmless

This was explicitly guarded to avoid cross-environment contamination between frontend and backend deployment contexts.

---

## 10. Dependency Files You Should Know

### Backend dependency files

- `academic/models.py`
- `academic/views.py`
- `academic/network_views.py`
- `academic/public_profile_views.py`
- `academic/admin_views.py`
- `academic/admin_paper_views.py`
- `academic/urls.py`
- `scoupdb/settings.py`

### Frontend dependency files

- `src/App.tsx`
- `src/components/Home.tsx`
- `src/components/SearchResults.tsx`
- `src/utils/searchEngine.ts`
- `src/utils/publicData.ts`
- `src/utils/datasetNormalization.ts`
- `src/components/CapabilitiesPage.tsx`
- `src/components/FacultyProfilePage.tsx`

### Operational files

- `OPERATIONS_LOG.md`
- `SCOUP_AGENT_HANDOFF.txt`
- `docs/semantic-search-notes.md`
- `docs/website-overview.md`
- `docs/frontend-overview.md`

---

## 11. Practical Operating Checklist

When deploying changes or debugging the platform:

1. Confirm the public dataset still respects `review_status='approved'` only.
2. Verify the frontend is not accidentally sourcing a stale `.env` value.
3. Ensure the query logic still enforces every multi-term match requirement.
4. Check the keyword document-frequency approach if a search starts returning generic results again.
5. Confirm admin approval flows remain superuser-only when the queue is intended to be private.
6. Verify the UI's filter pipeline still respects `faculty/paper/patent/project` toggles.
7. Back up DB state before any write-heavy cleanup or restore operation.

---

## 12. Current Reference Summary

The current platform is a hybrid of:

- a real research-discovery frontend
- a Django-backed trust boundary and review system
- a stricter-than-default search engine tuned for noisy metadata
- human review queues for the ambiguous records that cannot be confidently auto-published

This architecture is intentionally conservative. It prioritizes trustworthiness over raw volume, which is why the recent data cleanup and review model were necessary before a broader public launch.

---

## 13. Recommended Presentation Talking Points

If you present this project, emphasize:

- Search relevance was improved by reducing over-trust in noisy OpenAlex keyword labels.
- Public trust is protected via immutable approve-only filters and review queues.
- Faculty verification and paper verification are handled as a quality gate, not just a data import step.
- The platform is not simply a search engine; it is a discovery and collaboration system with a human review layer.
- Search and data policies were tuned for actual faculty research discovery rather than just indexing everything available.

---

This playbook is meant as the architecture reference for future work, presentations, and debugging. It is intentionally written to explain not just what exists, but why the current logic is designed the way it is.
