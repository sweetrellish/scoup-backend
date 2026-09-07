# Copilot Instructions — scoup-backend

## What this is
Django backend for the SCOUP research-discovery platform (single app: `academic/`). It serves public search/discovery data (faculty, papers, projects, patents) and admin review/approval workflows. See `README.md` for full architecture detail — this file summarizes it and adds working conventions.

## Architecture essentials
- **Trust/review model**: the DB has two layers — approved public data vs. pending/rejected review data. Only `review_status='approved'` records are exposed in public search, for both faculty and papers. Never bypass this filter when touching public-facing queries.
- **Search relevance** (`academic/views.py`): conservative by design — word-boundary matching, all-terms-required for multi-word queries, title > abstract/theme > keyword weighting, `_keyword_trust()` and `_keyword_specificity()` discount noisy/broad keyword sets, `_MIN_CONFIDENCE` blocks weak matches. This exists to prevent false-positive matches from broad upstream tags — don't loosen it without understanding why.
- **Admin review queues**: `admin_views.py` (faculty), `admin_paper_views.py` (papers) — restricted to superusers, not generic staff. Keep permission checks explicit rather than relying on a general staff role.
- **Data-integrity commands** live in `academic/management/commands/` (verify/purge/restore scripts) — these are part of the trust layer, not cosmetic cleanup scripts.
- Key files: `academic/models.py`, `academic/views.py` (`public_search_data()` assembles the public payload), `academic/urls.py`.

## Build / run / test
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
python manage.py check          # health/routing check
python manage.py test           # existing tests in academic/tests.py (TestCase-based)
```
Deploy path (`build.sh`): `pip install` → `collectstatic --no-input` → `migrate`. There is no test step in the deploy script — run `python manage.py test` manually before deploying model/view/permission changes.

## Operational rules (from README, keep enforcing these)
- Never source/import an unaudited `.env` file into a shell without reading it first. A stray `VITE_API_BASE_URL` line once got into this backend's `.env` and caused a real, hard-to-diagnose login/API-routing bug — when a login or API-connectivity issue shows up, check `.env` and actual runtime config *before* theorizing about client-side causes (browser, VPN, network, caching).
- Back up the database before any repair/purge/data-integrity operation.
- Keep public search strictly filtered to `review_status='approved'`.
- Treat broad/upstream metadata as weak evidence unless corroborated — don't treat it as ground truth.

## Working conventions (from prior session friction)
- **Confirm UI/behavior scope before implementing, especially for "match this to that" requests** (e.g., porting nav bar functionality from one page to another). Explicitly confirm whether desktop and mobile should be unified or kept independent, and whether existing look/feel must be preserved — assuming unification or a particular interpretation has caused repeated rework.
- **Don't abbreviate or otherwise alter labels/copy that weren't asked to be changed.** If shortening a label seems necessary for layout, ask first instead of silently truncating it.
- **Watch for regressions on unrelated, previously-set values** when making incremental style/CSS edits (e.g., an edit to a gradient/overlay unintentionally changed a logo's size that had already been fixed). Call out explicitly which properties an edit touches so unintended side effects are easy to spot before deploying.
- **Prefer local verification over full production deploys for iterative visual/CSS changes.** The deploy script (`sudo ./scoupsite-pushv4.sh`) is a full production push, not a preview step — batch small visual tweaks and verify locally (dev server/build) where possible instead of doing a deploy-screenshot-adjust loop for every minor spacing/color/sizing change; this has previously burned a large amount of time and cost across many round trips in a single session.

## Repo hygiene
The repo root has accumulated generated/operational artifacts (screenshots, PDF/JSON exports, `db.sqlite3` backups, log files, session transcripts). Avoid adding new generated artifacts to the repo root — prefer a gitignored scratch location, and don't commit database backups, logs, or export dumps alongside source code.
