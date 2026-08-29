# SCOUP Backend — Operations & Rollback Log

Authoritative record of backend, database, and server-level changes.
Frontend changes are logged separately in `~/scoup-frontend-2.0/OPERATIONS_LOG.md`.

## How to use this log

Every entry has a **Restore ID**. To recover from a critical failure, find the last
entry whose state you want, then run the restore command listed in that entry.

Restore ID formats:

| Prefix | Meaning | Location |
| --- | --- | --- |
| `DB-*` | SQLite database snapshot | `~/scoup-backups/` |
| `SITE-*` | Full site backup made by the deploy manager | `/var/www/scoup2025.privatedns.org/_backups/` |
| `NONE` | No artifact required (process/config-only change, reversible in place) | |

Quick restore of a database snapshot:

```bash
sudo systemctl stop scoup-gunicorn
cp ~/scoup-backups/<snapshot-file> /var/www/scoup2025.privatedns.org/scoup-backend/db.sqlite3
sudo systemctl start scoup-gunicorn
```

Quick restore of a full site backup: use `~/scoupsite-pushv4.sh` → option `4` (Rollback to backup).

---

## Environment reference

| Item | Value |
| --- | --- |
| Live site | <https://scoup-salisbury.net> |
| Served backend | `/var/www/scoup2025.privatedns.org/scoup-backend` |
| Live service | `scoup-gunicorn.service` → `127.0.0.1:8000` (4 workers) |
| Staging service | `scoup-backend-staging.service` → `127.0.0.1:9000` |
| Staging source | `/home/rellis/scoup-backend` (this git repo) |
| Frontend build | `/var/www/scoup2025.privatedns.org/dist` |
| Frontend source | `/home/rellis/scoup-frontend-2.0` |
| Database | `db.sqlite3` (~9.5 MB) |

---

## Entries

### 2026-08-28 13:52 — Pre-change database snapshots

- **Restore ID:** `DB-20260828-135222`
- **Type:** Backup
- **Description:** Safety snapshots taken before any remediation work began.
- **Artifacts:**
  - `~/scoup-backups/db.sqlite3.pre-fix.20260828-135222` (from `/home/rellis/scoup-backend`)
  - `~/scoup-backups/varwww-db.sqlite3.pre-fix.20260828-135222` (from live `/var/www`)
- **Restore:**

  ```bash
  sudo systemctl stop scoup-gunicorn
  cp ~/scoup-backups/varwww-db.sqlite3.pre-fix.20260828-135222 \
     /var/www/scoup2025.privatedns.org/scoup-backend/db.sqlite3
  sudo systemctl start scoup-gunicorn
  ```

### 2026-08-28 13:54 — Cleared stale workers holding port 8000 (CRITICAL FIX)

- **Restore ID:** `NONE` (no artifact; state is self-correcting via systemd)
- **Type:** Server / process
- **Files changed:** none
- **Problem:** `scoup-gunicorn.service` was stuck in `activating (auto-restart)` and had been
  failing continuously with `[Errno 98] Address already in use`. Port 8000 was held by three
  orphaned Gunicorn workers (PIDs 2673627, 2673642, 2673643, started 09:15:30) whose working
  directory resolved to `/var/www/scoup2025.privatedns.org/scoup-backend (deleted)`. They were
  serving a **deleted build**, so every source edit made to the deployed backend had no effect.
- **Impact:** This was the true cause of the "categories endpoint returns TemplateDoesNotExist"
  issue documented in `SCOUP_AGENT_HANDOFF.txt` as the outstanding blocker. The endpoint code was
  correct; it was simply never executed.
- **Action:** Terminated the three orphaned PIDs. systemd then bound port 8000 successfully.
- **Verification:**
  - `systemctl is-active scoup-gunicorn` → `active`
  - `GET http://127.0.0.1:8000/api/categories/` → `200 application/json`
  - `GET https://scoup-salisbury.net/api/categories/` → `200 application/json`
- **Rollback:** Not applicable. The terminated processes ran deleted code and cannot (and should
  not) be restored. To stop the service: `sudo systemctl stop scoup-gunicorn`.
- **Recurrence prevention:** If port 8000 is ever occupied again, verify ownership before
  restarting:

  ```bash
  ss -ltnp | grep :8000
  for p in $(pgrep -f "gunicorn scoupdb.wsgi"); do echo "$p -> $(readlink /proc/$p/cwd)"; done
  ```

  Any process whose `cwd` ends in `(deleted)` is stale and safe to terminate.

### 2026-08-28 14:04 — Backend feature work (search, categories, DEBUG)

- **Restore ID:** `SRC-20260828-1404`
- **Type:** Source code
- **Artifacts (rollback copies of the pre-change file):**
  - `~/scoup-backups/views.py.before-categories.*`
  - `~/scoup-backups/views.py.before-rank.*`
- **Files changed:**
  - `academic/views.py` — ranked search + category endpoints
  - `academic/urls.py` — route registration
  - `scoupdb/settings.py` — DEBUG hardening
- **Restore:**

  ```bash
  cp ~/scoup-backups/views.py.before-rank.<timestamp> \
     /home/rellis/scoup-backend/academic/views.py
  cd /home/rellis/scoup-backend && ./.venv/bin/python manage.py check
  ```

**1. Search relevance rewritten.** The previous fallback matched only
`title__icontains` / `abstract__icontains` and **never searched `keywords`**, where the
taxonomy actually lives. `"computer science"` matched just 2 papers, so the frontend padded
results with loose matches — the source of the skew you reported.

Replaced with weighted multi-field scoring:

| Field | Weight | Rationale |
| --- | --- | --- |
| `title` | 5.0 | Author-written, most reliable |
| `themes` | 3.0 | Specific topical tags |
| `abstract` | 2.5 | Author-written |
| `keywords` | 2.0 | Machine-assigned upstream, noisy |
| `journal` | 1.0 | Weak signal |

- All query terms must match (word-boundary, not substring), so partial matches on a
  single common word like "science" no longer qualify.
- Exact keyword match adds +25; phrase-in-title adds +15.
- Results below confidence 30 are dropped rather than padded to fill `limit`.
- Citations and recency are **tiebreakers only**, so they cannot outrank relevance.
- Generic umbrella tags (`nec`, `, other`, `, general`, `Interdisciplinary computer sciences`)
  are halved when they are the *only* evidence. Cause: the upstream classifier tags broadly —
  e.g. "Mobile Journalism as Lifestyle Journalism?" carries `Interdisciplinary computer sciences`.
- Responses now include `confidence` and `matchedOn` for explainability.

**2. Categories implemented.** Replaced the hardcoded stub `{"categories": ["CS","Bio"]}`.
Note `top/mid/low_level_categories` are **empty for every record**; the live taxonomy is in
`Paper.keywords` and `Faculty.categories`, so the endpoints aggregate those.

- `GET /api/categories/` → 333 categories with `paperCount`, `facultyCount`, `slug`.
  Supports `?q=`, `?limit=`, `?min_count=`.
- `GET /api/categories/<name-or-slug>/` → papers + faculty; 404 with `detail` if unknown.

**3. `DEBUG` hardened.** Was `os.environ.get("DEBUG","") != "False"`, which defaulted to
**True** and served full tracebacks publicly. Now secure by default (explicit opt-in only).
Verified `settings.DEBUG` resolves to `False`.

- **Verification:** `manage.py check` → 0 issues; `/api/categories/computer-science/` →
  200, 31 papers / 87 faculty; unknown category → 404.
- **Status:** Applied to the canonical repo. **Not yet deployed to `/var/www`.**

### 2026-08-28 13:55 — Added backup deletion to deploy manager

- **Restore ID:** `SCRIPT-20260828-135546`
- **Type:** Tooling
- **File changed:** `~/scoupsite-pushv4.sh`
- **Artifact:** `~/scoup-backups/scripts/scoupsite-pushv4.sh.20260828-135546`
- **Change:** Added menu option `8  Delete backups (free disk space)` with three modes —
  delete by number, keep newest N, or delete older than N days.
- **Safety:** Requires typing `DELETE` to confirm, **always protects the most recent backup**,
  and logs every deletion to `~/.scoup-deploy.log`.
- **Restore:** `cp ~/scoup-backups/scripts/scoupsite-pushv4.sh.20260828-135546 ~/scoupsite-pushv4.sh`
- **Verification:** `bash -n` syntax check passed.

### 2026-08-28 14:29 - API contract alignment + SPA fallback fix

- **Restore ID:** `SRC-20260828-1429`
- **Type:** Source code
- **Artifacts:** `~/scoup-backups/views.py.before-contract.*`
- **Files changed:** `academic/views.py`, `academic/urls.py`, `scoupdb/urls.py`

**1. Deploy activation.** The 14:14 deploy copied files correctly, but Gunicorn workers had
started at 13:54 and were still serving pre-deploy code from memory. Sent SIGHUP to the master;
systemd restarted the unit at 14:23 (~11s of 502). Live now serves the new code.

**2. TemplateDoesNotExist: index.html - real root cause.** `scoupdb/urls.py` had a catch-all SPA
fallback `path('<path:resource>', TemplateView(index.html))` that swallowed any unmatched route,
including `/api/*`. No `templates/index.html` exists in the repo, and the `/var/www` copy is
`drwxr-x--- root root` (unreadable by the service), so unmatched API paths raised a template error
instead of 404. Replaced with a regex fallback excluding `api/`, `admin/`, `media/`, `static/`.
Unmatched API routes now return 404, not 500.

**3. Frontend/backend contract mismatch.** `scoup-frontend-2.0/src/utils/api.ts` expects a
different shape than the endpoints returned:

| | Frontend expects | Previously returned |
| --- | --- | --- |
| `/categories/` | bare array `TopLevelCategory[]` | `{categories: [...], count}` |
| fields | `article_count`, `faculty_count`, `mid_level_categories[]` | `paperCount`, `facultyCount` |
| `/categories/<slug>/` | `category_name`, `stats{}`, `themes[]` | `category`, flat counts |

Endpoints rewritten to match `api.ts` exactly. Verified `/categories/` returns 290 items and
`/categories/computer-science/` returns 31 papers / 87 faculty / citation_average 6.42.

**4. Category hierarchy is derived, not stored.** `top/mid/low_level_categories` are empty on every
record, so top-level grouping is derived from the segment before the first comma
(e.g. `Sociology, general` -> `Sociology`). Heuristic; the real hierarchy needs a re-import.

**5. Added `/api/query-expansions/`** returning an abbreviation->expansion map matching the
`Record<string, string>` contract in `BrowseCategories.tsx`.

- **Verification:** `manage.py check` clean; expansions 200 JSON; unknown API route 404.
- **Status:** In repo, NOT yet deployed.

### 2026-08-28 14:46 - Search relevance: affiliation stripping + confidence calibration

- **Restore ID:** `SRC-20260828-1446`
- **Type:** Source code
- **Artifacts:** `~/scoup-backups/views.py.before-affil.*`, `~/scoup-backups/views.py.before-calib.*`
- **File changed:** `academic/views.py`

**1. Affiliation boilerplate stripped.** Abstracts in this dataset embed author/affiliation
blocks, e.g. *"Department of Math and Computer Science, Salisbury University, 1101 Camden Ave"*.
A salt-marsh paper therefore ranked for "computer science". Added `_clean_abstract()`, which drops
lines matching a department+institution pattern and boilerplate headers (Affiliations, Authors,
DOI, Cited by, Notes on contributors, Acknowledgements) before scoring. Only offending lines are
removed, not the whole abstract.

**2. Confidence recalibrated.** Scores previously saturated at 100.0, making ranking meaningless.
Now blends three signals:

| Signal | Weight | Meaning |
| --- | --- | --- |
| density | 0.45 | strength of the best field per matched term |
| breadth | 0.25 | how many independent fields corroborate |
| coverage | 0.30 | share of query terms matched |

Bonuses: exact keyword +12, phrase-in-title +12, phrase-in-themes +6, phrase-in-abstract +3.

Observed spread for "machine learning": 96.3 / 83.2 / 73.2 / 60.1 (was all 100.0).

- **Verification:** `manage.py check` clean. "computer science" no longer returns the salt-marsh
  or geography papers; top hits are Computer-Assisted Data, Information Influence, LAX-Score,
  information systems.
- **Known limitation:** scoring is bag-of-words, so a multi-word concept can still score via
  separate fields (e.g. "computer" in journal, "science" in themes). Phrase-proximity scoring
  would fix this.
- **Status:** In repo, NOT yet deployed.

### 2026-08-28 15:12 - Phrase-proximity experiment REVERTED

- **Restore ID:** `SRC-20260828-1512`
- **Type:** Source code (reverted)
- **File:** `academic/views.py` restored from `~/scoup-backups/views.py.before-prox.150337`

**Attempted:** phrase-proximity scoring (`_best_proximity`) so query terms had to co-occur within
one field, to stop matches like "computer" in a journal name plus "science" in a theme tag.

**Result: rejected.** Two variants were tested and both underperformed:

| Variant | Effect |
| --- | --- |
| additive bonus | inflated loose matches ("How CPR is like Madonna" 80.2 -> 90.2) |
| multiplier | regressed a genuinely relevant paper (Self-Supervised Sensor Learning 83.2 -> 62.4) |

Root cause: relevant papers often mention query terms far apart in long abstracts, so span-based
proximity punishes true positives as hard as false ones.

**Action:** reverted to the deployed ranking. Repo and `/var/www` now match; no redeploy required.
Verified "computer science" 91.1/80.2/80.0/78.4 and "machine learning" 96.3/83.2/73.2/73.2,
identical to live.

**Future approach:** proper fix is a phrase/bigram index or embeddings (`OPENAI_API_KEY`), not
character-span heuristics.

### 2026-08-28 17:39 - SU directory ingestion + faculty enrichment

- **Restore ID:** `DB-20260828-1734` (database) / `SRC-20260828-1739` (source)
- **Type:** Data + source
- **Artifacts:**
  - `~/scoup-backups/db.sqlite3.pre-directory.*`
  - `~/scoup-backups/models.py.before-directory.*`
  - `~/scoup-backups/views.py.before-srcprofile-fix.*`
- **Files added:** `academic/directory_parser.py`,
  `academic/management/commands/import_su_directory.py`,
  `academic/migrations/0007_faculty_directory_verified.py`

**1. `SUdirectory.pdf` parsed.** The PDF has two sections: pages 1-42 list staff by department
(bold 10pt headers, fixed columns at x=54 name / 152 title / 418 room / 524 extension), and
pages 43+ repeat everything alphabetically with department folded into the title field. Parsing
by font weight and column position yields **1,849 entries across 132 departments**. Parsing stops
at the section boundary; an earlier text-only parser mis-assigned 1,814 rows to "Writing Center"
because wrapped title fragments looked like department headers.

**2. Faculty enrichment applied.** `import_su_directory` matches on last name plus first name
(exact) or first initial, and fills only blank fields unless `--overwrite` is passed. Dry-run is
the default.

| Result | Count |
| --- | --- |
| matched exact | 95 |
| matched by initial | 126 |
| ambiguous (needs admin review) | 96 |
| unmatched | 1,317 |
| **records updated** | **221** |

`title`, `department`, `room`, and `phone` now populate for 221 faculty and surface in
`/api/public/search-data/` (222 records now carry a department, up from ~0).

**3. Key insight for admin validation.** Only ~244 of 1,634 Faculty rows correspond to real SU
directory people. The rest are **external co-authors** imported from the publication dataset.
The new `directory_verified` boolean marks records confirmed against the official directory and
is the natural basis for the admin validation queue.

**4. Latent crash fixed.** Migration `0006` (authored outside this session) removed
`Faculty.source_profile`, but `_absorb_external_faculty()` in `views.py` still referenced it -
approving a faculty suggestion would have raised `AttributeError`. Those lines were removed.

**5. Scoring fields already exist.** Migrations 0005/0006 added `expertise`, `academic`,
`practice`, `publication` to Faculty - these map to the Acad/Prac/Pub bars in the Interlora
screenshots. `collaboration` and `network` do not yet exist.

- **Verification:** `manage.py check` clean; `/api/public/search-data/`, `/api/categories/`,
  `/api/query-expansions/` all 200.
- **Status:** In repo + repo DB. **NOT yet deployed;** the live DB also needs
  `manage.py migrate` and a directory import run.

### 2026-08-28 18:48 - Network discovery + inquiry surface

- **Restore ID:** `SRC-20260828-1848`
- **Type:** Source + schema
- **Artifacts:** `~/scoup-backups/models.py.before-inquiry.*`
- **Files added:** `academic/network_views.py`, `academic/migrations/0008_networkinquiry.py`
- **Files changed:** `academic/models.py`, `academic/urls.py`

**Endpoints implemented** (matching `NetworkPage.tsx` / `api.ts` contracts):

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /api/network/discovery/?q=&limit=` | optional | colleagues, papers, patents, projects + profileKeywords/suggestedCategories/expandedTerms |
| `POST /api/network/inquire/` | optional | intro request; anonymous callers must supply name + email |
| `GET /api/faculty/inquiries/` | required | inquiries addressed to the signed-in faculty |
| `PATCH /api/faculty/inquiries/<id>/` | required | set status new/reviewed/closed |

Discovery personalises from the signed-in faculty profile when no `q` is supplied, and returns
`matchScore` + `sharedKeywords` + `matchReason` so the UI can explain each result. It also returns
`directoryVerified`, so verified SU faculty can be visually distinguished from imported external
co-authors.

**Security controls on the unauthenticated write path.** `POST /network/inquire/` is the first
endpoint that accepts writes without auth, so it enforces:

- required + RFC-validated `requester_email`, required `requester_name`
- note truncated to 4,000 chars; all string fields length-capped to column widths
- **5 submissions per IP per hour**, returning HTTP 429 (verified: 6th request throttled)
- requester IP stored solely for throttling
- ownership check on PATCH - only the target faculty or staff may change status

**New model `NetworkInquiry`** stores target, requester, shared keywords, note, and status.

- **Verification:** `manage.py check` clean; discovery 200 with ranked colleagues/papers;
  inquire 201; invalid email 400; missing target 400; throttle 429.
- **Status:** In repo. **NOT yet deployed** - live needs `migrate` for `0008_networkinquiry`.

### 2026-08-28 19:05 - Admin faculty validation surface

- **Restore ID:** `SRC-20260828-1905`
- **Type:** Source + schema
- **Artifacts:** `~/scoup-backups/models.py.before-admin.*`
- **Files added:** `academic/admin_views.py`,
  `academic/migrations/0009_faculty_institutional_email_and_more.py`
- **Files changed:** `academic/models.py`, `academic/urls.py`

**Endpoints** (all `IsAdminUser`, matching `adminAPI` in `api.ts`):

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/admin/me/` | GET, PATCH | admin profile |
| `/api/admin/stats/` | GET | faculty/content/inquiry counts + department breakdown |
| `/api/admin/faculty/` | GET | filter by `search`, `status`, `department`, `pending` |
| `/api/admin/faculty/<id>/` | GET, PATCH, DELETE | record management |
| `/api/admin/faculty/<id>/approve/` | POST | approve |
| `/api/admin/faculty/<id>/reject/` | POST | reject with reason |
| `/api/admin/faculty/bulk-action/` | POST | bulk approve/reject |
| `/api/admin/faculty/<id>/message/` | POST | record admin -> faculty message |
| `/api/admin/inquiries/` | GET | inquiry triage |
| `/api/admin/inquiries/<id>/` | PATCH | status + admin notes |
| `/api/admin/audit-log/` | GET | recent review activity |

**New Faculty fields:** `review_status` (pending/approved/rejected), `review_note`,
`institutional_email`, `institutional_email_verified`, `last_active`.
**New NetworkInquiry fields:** `admin_notes`, `message_subject`, `source_type`, `reviewed_by`.

Backfilled `review_status='approved'` for the 1,632 already-approved records.

**Validation queue now measurable:**

| Metric | Count |
| --- | --- |
| total faculty | 1,634 |
| approved | 1,632 |
| pending | 2 |
| directory_verified | 221 |
| unverified (mostly external co-authors) | 1,413 |

`status=verified` / `status=unverified` filters let admins separate genuine SU faculty from
imported external co-authors.

**Access control verified:** anonymous -> 401, authenticated non-staff -> 403, staff -> 200.
Invalid bulk action -> 400.

**Note:** smoke-test inquiries and users created during verification were deleted, because the
deploy script copies `db.sqlite3` to production and they would otherwise ship as real data.

- **Verification:** `manage.py check` clean; stats/list/inquiries/audit/me all 200.
- **Status:** In repo. **NOT yet deployed** - live needs `migrate` for `0009`.

### 2026-08-29 00:19 - Discovery default ranking + directory match accuracy fix

- **Restore ID:** `DB-20260829-0018` (database) / `SRC-20260829-0019` (source)
- **Artifacts:** `~/scoup-backups/db.sqlite3.pre-strict.*`,
  `~/scoup-backups/network_views.py.before-default-rank.*`
- **File changed:** `academic/network_views.py`

**1. Browse view was meaningless.** `/network/discovery/` with no `q` produced an empty seed, so
every colleague scored **0.0** and the list was ordered purely by citations - dominated by
imported external co-authors (Friess, Rogers, Lovelock) with blank departments. Only 9 of the
first 40 were SU faculty. This is what made the Experts page look unremarkable.

Added `_default_prominence()` for the no-query case: directory-verified +45, has department +10,
has title +5, article count up to +20, citations up to +20. Sorting now puts
`directoryVerified` first, then score, then citations. `matchReason` distinguishes verified SU
faculty from publication-record entries. Query-driven ranking is unchanged.

**2. Directory matching was over-trusting (data accuracy).** Of the 221 records marked
`directory_verified`, only **95 were exact first-name matches**; **126 were first-initial only**.
Spot check found a clear false positive: *Shing Yip Lee*, a mangrove ecologist and external
co-author, was matched to a "Lee, S..." row and labelled **Assistant Professor, Physics** - and
that wrong department was being served publicly.

Corrected: initial-only matches were demoted to `directory_verified=False` and
`review_status='pending'`, and the fields written from those matches were cleared - but only
where the stored value still equalled what the importer wrote, so no unrelated data was touched.

| | Before | After |
| --- | --- | --- |
| directory_verified | 221 | 95 (exact only) |
| pending review | 2 | 128 |
| fields cleared | - | 463 |

Verified afterwards: *Shing Yip Lee* has no department/title and is pending review; the default
browse view now returns only genuine SU faculty (Exercise Science, Biological Sciences,
Marketing, Psychology, Economics, Physics).

**Follow-up:** the 126 demoted records are legitimate review candidates for the admin queue via
`/api/admin/faculty/?status=pending`. `import_su_directory` should default to exact-match-only
for verification; initial matches belong in review, not auto-applied.

- **Status:** In repo + repo DB. **NOT yet deployed.**

### 2026-08-29 08:59 - import_su_directory now exact-match-only by default

- **Restore ID:** `SRC-20260829-0859`
- **Artifact:** `~/scoup-backups/import_su_directory.py.before-strict.*`
- **File changed:** `academic/management/commands/import_su_directory.py`

Makes the safe behaviour the default, so the *Shing Yip Lee -> Physics* class of false positive
cannot recur:

- Only **exact first-name** matches are written and earn `directory_verified=True`.
- **First-initial-only** matches are no longer written. They are counted, reported, and their
  Faculty rows are set to `review_status='pending'` so they appear in the admin queue at
  `/api/admin/faculty/?status=pending`.
- `--include-initial` re-enables the old behaviour explicitly, with the risk documented in
  `--help`.

Dry-run comparison:

| Mode | exact | initial written | held for review |
| --- | --- | --- | --- |
| default | 95 | 0 | 126 |
| `--include-initial` | 95 | 126 | 0 |

- **Verification:** both modes dry-run clean; `py_compile` passes.
- **Status:** In repo. Behaviour-only change; no migration required.

---

## Known open items

| # | Item | Severity | Status |
| --- | --- | --- | --- |
| 1 | `DEBUG` resolves to `True` in production; tracebacks are publicly exposed | High (security) | Open |
| 2 | `/api/categories/` still returns hardcoded stub `["CS", "Bio"]` | High | Open |
| 3 | Search relevance is unweighted substring matching; returns irrelevant results | High | Open |
| 4 | `/var/www` backend and `/home/rellis/scoup-backend` have diverged | Medium | Open |
| 5 | `/var/www/.../scoup-backend/templates` is `drwxr-x--- root root`, unreadable by the service | Low | Open |
