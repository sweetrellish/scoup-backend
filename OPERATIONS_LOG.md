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

### 2026-08-29 09:25 - salisbury.edu ingestion: schools + department mapping

- **Restore ID:** `DB-20260829-0925` (database) / `SRC-20260829-0925` (source)
- **Artifacts:** `~/scoup-backups/db.sqlite3.pre-schools.*`, `~/scoup-backups/models.py.before-school.*`
- **Files added:** `data/su_schools.json`,
  `academic/management/commands/import_su_schools.py`,
  `academic/migrations/0010_faculty_school.py`
- **Files changed:** `academic/models.py`, `academic/network_views.py`

**Fetching note.** `salisbury.edu` pages redirected the built-in fetcher to an ad-tracking pixel
(`pixel.tapad.com` -> `tr.snapchat.com`) and returned no content. Retrieved with `curl` and a
browser user-agent instead; all pages returned HTTP 200.

**Extracted the six academic units** from `colleges-schools-and-departments.aspx`, then pulled
department lists from each `academic-offices/<slug>/` page:

| School | Departments |
| --- | --- |
| College of Health and Human Services | 9 |
| Fulton School of Liberal Arts | 11 |
| Henson School of Science & Technology | 6 |
| Perdue School of Business | 6 |
| Seidel School of Education | 3 |

Saved to `data/su_schools.json` (35 departments). Clarke Honors College lists no departments.

**New `Faculty.school` field**, populated by `import_su_schools` (dry-run by default). Matching
folds the `&` vs `and` spelling difference between the directory PDF and the website, and falls
back to a word-stem match for cases like "Mathematics" vs "Mathematical Sciences".

Result: **94 of 96** faculty with a department resolved to a school. The 2 remaining are a record
whose department is literally "Fulton School of Liberal Arts" and "Urban&Regional Planning
Program".

`network/discovery` now returns the real `school` (previously it incorrectly returned `office`,
which is an office location, so the field was effectively empty).

**Not obtained:** the research page is navigational only and lists no labs or centers, so Labs
still has no data source. One facility was identified incidentally: Henson Medical Simulation
Center.

- **Verification:** `manage.py check` clean; discovery returns correct school per colleague.
- **Status:** In repo + repo DB. **NOT yet deployed** - live needs `migrate` for `0010`.

### 2026-08-29 09:40 - Facilities + Institutions datasets

- **Restore ID:** `SRC-20260829-0940`
- **Files added:** `data/su_facilities.json`, `data/institutions.json`

**Facilities (37).** Parsed from `discover-su/campus-map/about-salisbury-facilities.aspx`:
Holloway Hall, Perdue Hall, Devilbiss Hall, Henson Science Hall, Guerrieri Academic Commons,
Nabb Research Center, Maggs Physical Activities Center, Sea Gull Stadium, etc.

**Building codes were NOT obtained.** The page has no systematic code list - the only
abbreviations present were noise (`ATM`, `GIS`, `NMR`). Room prefixes in the directory
(`HH`, `PH`, `DH`, `HS`, `AC`, `TE`, `GSU`) can be inferred by initials, but that would be a
guess, so no code mapping was written. Facilities can currently be listed, not joined to people.

**Institutions (149) derived from paper affiliations,** not scraped - the correct source, as
external institutions only appear there.

| Institution | Mentions |
| --- | --- |
| Salisbury University | 117 |
| Vanderbilt University | 13 |
| Rensselaer Polytechnic | 7 |
| University of Illinois | 7 |
| University of Maryland Eastern Shore | 6 |

**Known extraction noise** to clean before shipping an Institutions page: names bleed into
matches ("Dean J. Kotlowski Salisbury University"), and multi-part names truncate
("University of Foreign Studies" from "Hankuk University of Foreign Studies").

- **Status:** Data files only. No model, endpoint or migration yet.

### 2026-08-29 09:50 - Deploy no longer overwrites the live database (CRITICAL)

- **Restore ID:** `SRC-20260829-0950`
- **Artifact:** `~/scoup-backups/scoupsite-pushv4.sh.before-rsync.*`
- **Files changed:** `~/scoupsite-pushv4.sh`
- **File added:** `data/su_building_codes.json` (105 codes)

**The fix.** `deploy()` ran `cp -r "$BACKEND_SRC" "$DOMAIN_DIR/scoup-backend"`, which copied
`db.sqlite3` over production on every deploy. Any faculty signup, photo upload, CV upload or
network inquiry created on the live site would have been destroyed by the next push. Replaced
with `rsync -a --delete` excluding `db.sqlite3`, `media/`, `.venv/`, `.git/` and `__pycache__/`.

Excluding `.git/` also stops the deployed copy from carrying the GitHub remote, which is what
made `/var/www` appear "ahead 4" and caused the VS Code sync confusion.

**WORKFLOW CHANGE - important.** Schema and data changes no longer reach production by riding
along in the copied database. After each deploy:

```bash
cd /var/www/scoup2025.privatedns.org/scoup-backend
sudo -u www-data ./.venv/bin/python manage.py migrate
```

Data imports (`import_su_directory`, `import_su_schools`) must also be run against the live
database, not just locally.

**Building codes (105)** extracted from `campus-map/building-info.aspx`: AC=Academic Commons,
HH=Holloway Hall, PH=Perdue Hall, DH=Devilbiss Hall, CK=Choptank Hall, etc. This is the join key
that was missing yesterday, so room numbers in the directory can now resolve to building names.

- **Verification:** `bash -n` passes; `rsync` present on host.

### 2026-08-29 10:02 - OpenAlex backfill agent (replaces Scholar scraping)

- **Restore ID:** `DB-20260829-1002`
- **Artifact:** `~/scoup-backups/db.sqlite3.pre-openalex.*`
- **File added:** `academic/management/commands/import_openalex.py`

**Why not Google Scholar.** Scholar is not open source, has no public API, forbids automated
access in its ToS, and defends aggressively against bots (CAPTCHA, IP bans) - a persistent worker
would be blocked quickly, from university infrastructure. OpenAlex is free, CC0-licensed and has
a documented API, so it gives the same outcome without the legal or blocking risk.

**Reconnaissance.** Salisbury University = OpenAlex `I9364636`, ROR `029gwvs11`:
**9,976 works / 337,629 citations**, against 665 papers locally - roughly 15x available.
Recent coverage: 187 works in 2026, 227 in 2025, 250 in 2024.

**First backfill applied (2026 only):**

| | |
| --- | --- |
| works fetched | 181 |
| new papers written | 181 |
| already present | **0** (confirming the DB had no 2026 research at all) |
| skipped, no DOI | 6 |
| author links created | 72 |
| total papers | 665 -> **846** |

Field mapping: title, DOI, journal, publication date, citation count, OA PDF URL, concepts ->
`keywords`, topics -> `themes`, SU authorships -> `faculty_members`. Abstracts are rebuilt from
OpenAlex's inverted index. Paginated by cursor with a 0.2s delay for the polite pool.

**Known gap:** 168 SU authors on these works are not in the Faculty table, and name forms differ
("A. Shakur" vs "Asif Shakur"), so only 72 links were made. Author matching needs initial-aware
logic - ORCID via OpenAlex would resolve this properly.

- **Usage:** `--since YYYY-MM-DD`, `--max N`, `--apply`; dry-run by default.
- **Status:** In repo + repo DB. **NOT yet deployed** - and since deploy no longer copies the DB,
  this import must be run against the live database separately.

### 2026-08-29 10:07 - ORCID-based author resolution

- **Restore ID:** `SRC-20260829-1007`
- **Artifacts:** `~/scoup-backups/models.py.before-orcid.*`,
  `~/scoup-backups/import_openalex.py.before-orcid.*`
- **Files changed:** `academic/models.py`, `academic/management/commands/import_openalex.py`
- **Migration:** `0011_faculty_openalex_id_faculty_orcid`

Added `Faculty.orcid` and `Faculty.openalex_id` plus an `AuthorResolver` that matches in
priority order: **ORCID -> OpenAlex author id -> exact name -> last name + first initial**.
Ambiguous initials (two faculty sharing surname and initial) resolve to no match rather than
guessing, which is the mistake the SU directory import made earlier.

Identities are persisted on write, so each run gets more accurate:

| Run | orcid | openalex | name | initial | total linked |
| --- | --- | --- | --- | --- | --- |
| before | - | - | 72 | - | 72 |
| after first apply | 0 | 0 | 72 | 19 | 91 |
| subsequent run | **44** | **38** | 8 | 1 | 91 |

21 faculty now carry an ORCID and 51 an OpenAlex id. Matching is no longer dependent on
name formatting.

**Site metrics verified.** `/api/public/search-data/` returns facultyData 1634, papersData
**846** (181 of them 2026). `Home.tsx` derives its counters from these array lengths, so the
front-page figures update automatically on deploy. `patentsData` and `projectsData` are both
**0** - those panels will render zeros until patent/project sources are ingested.

- **Status:** In repo + repo DB. Live needs `migrate` (0010, 0011) plus the import run.

### 2026-08-29 10:18 - INCIDENT: production outage during deploy (resolved)

- **Restore ID:** `INCIDENT-20260829-1018`
- **Artifacts:** `~/scoup-backups/scoupsite-pushv4.sh.before-rmfix.*`,
  `~/scoup-backups/scoupsite-pushv4.sh.before-chownfix.*`
- **Impact:** `/api/*` returned 502 for roughly 15 minutes. The static home page kept serving.
  **No data was lost.**

**Cause 1 - deletion.** `deploy()` had always run `sudo rm -rf "$DOMAIN_DIR/scoup-backend"`
immediately before copying. That was harmless with `cp -r`, which recreated everything from
source. The `rsync` change added earlier the same day *excludes* `db.sqlite3` and `.venv/`, so
after the `rm -rf` wiped them rsync correctly refused to re-create excluded paths - leaving no
virtualenv and no database. Changing a copy command's semantics without auditing the surrounding
cleanup was the mistake.

**Cause 2 - ownership.** Rebuilding the venv with `sudo -u www-data` created it `0770
www-data:www-data`, but `scoup-gunicorn.service` runs as **`User=rellis`**. systemd reported
`status=203/EXEC ... Permission denied`. The database had the same problem (`0640 www-data`).
The previous venv worked only because its modes were permissive enough for cross-user execution.

**Resolution.**

1. Removed the `rm -rf` from `deploy()`; `rsync --delete` already removes stale files while
   preserving excluded ones.
2. Rebuilt the venv and restored `db.sqlite3` from the repo copy.
3. `chown -R rellis:rellis` on `.venv` and `db.sqlite3` to match the service user.
4. Changed both deploy `chown` calls from `www-data:www-data` to
   `"${SUDO_USER:-rellis}":www-data` plus `chmod -R u+rwX,g+rX,o+rX`, so the service user can
   execute and nginx can still read. Without this the next deploy would have re-broken it.

**Verified after recovery:** `/`, `/api/categories/`, `/api/query-expansions/`,
`/api/network/discovery/`, `/api/search/` all **200**; 1,634 faculty and 846 papers live,
including the 181 from 2026.

**Lesson:** when swapping a recursive copy for an excluding sync, audit what runs before it -
exclusions only protect files that still exist.

### 2026-08-29 10:41 - Full OpenAlex backfill + scaled category threshold

- **Restore ID:** `DB-20260829-1039` (database) / `SRC-20260829-1041` (source)
- **Artifacts:** `~/scoup-backups/db.sqlite3.pre-fullbackfill.*`,
  `~/scoup-backups/views.py.before-mincount.*`
- **File changed:** `academic/views.py`

**1. Full historical backfill applied** (all years, not just 2026):

| | |
| --- | --- |
| works fetched | 9,237 |
| new papers | 8,392 |
| existing updated | 845 |
| skipped, no DOI | 376 |
| author links created | 3,559 |
| **total papers** | 846 -> **9,237** |
| runtime | 78 seconds |

Author resolution across the full corpus: name 2,701, initial 388, orcid 377, openalex 93.
Faculty carrying an ORCID went 21 -> **226**; OpenAlex ids 51 -> **535**. 2,978 papers have at
least one linked SU author. Year coverage is now continuous: 2026 (181), 2025 (225), 2024 (250),
2023 (220), 2022 (214), 2021 (240).

5,358 SU author names on these works still have no Faculty row - they are external co-authors
plus SU people who were never imported. That is the remaining input to the admin review queue.

**2. Category threshold now scales with the corpus.** A fixed floor was wrong twice in one
session: `min_count=3` yielded 439 groups at 846 papers, then 4,245 once the corpus reached
9,237. The default is now `max(5, total_papers // 300)`, so it tracks the data instead of
drifting.

| Corpus | Auto threshold | Categories shown |
| --- | --- | --- |
| 9,237 papers | 30 | **390** |

`?min_count=` still overrides (e.g. `min_count=100` -> 142, `min_count=1` -> 13,161).

Top categories are now real disciplines: Biology (3,330), Medicine (2,758), Computer science
(2,732), Psychology (1,609), Chemistry (1,438). `/categories/computer-science/` reports 2,732
papers, 87 faculty, 49,480 citations, average 18.11.

- **Verification:** `manage.py check` clean; categories 390 by default; detail endpoint intact;
  search "machine learning" returns four genuinely ML papers.
- **Status:** In repo + repo DB. **NOT yet deployed** - live needs the import run separately,
  since deploy no longer copies the database.

### 2026-08-29 11:03 - Dual-source faculty creation + metric scoping

- **Restore ID:** `DB-20260829-1102` (database) / `SRC-20260829-1103` (source)
- **Artifacts:** `~/scoup-backups/db.sqlite3.pre-newfaculty.*`,
  `~/scoup-backups/views.py.before-sufilter.*`
- **Files changed:** `academic/views.py`, `academic/admin_views.py`

**1. 87 SU faculty created from dual-source evidence.** Cross-referencing OpenAlex authorships
against the SU directory found 119 author names that are listed SU academics but had no Faculty
row. Evidence is two independent sources agreeing: OpenAlex records the SU institution
affiliation (`I9364636`) *and* the directory lists them with an academic title.

Created with `directory_verified=True`, `review_status='approved'`, and directory-sourced title,
department, school, room and phone. `review_note` records the provenance so any correction is
traceable. **7 candidates were skipped** because more than one directory row shared the surname +
first initial - ambiguity resolves to no record rather than a guess.

| | |
| --- | --- |
| candidates | 94 |
| created | **87** |
| skipped (ambiguous) | 7 |
| faculty total | 1,634 -> 1,721 |
| directory_verified | 95 -> **182** |

**2. Metrics were counting external co-authors as faculty.** The Faculty table holds
**1,537 external co-authors** imported from publication data alongside **184 real SU people**.
Public counters therefore reported 1,721 "faculty", and the department chart showed *Unassigned*
as the largest group, because those co-authors have no department.

Added `_su_affiliated()` - directory-verified, or has a login, or has a department - and applied
it to `_visible_faculty_qs()` and `public_search_data`. External co-authors remain in the
database for paper attribution but no longer appear as faculty or in metrics.

| Metric | Before | After |
| --- | --- | --- |
| facultyData | 1,721 | **184** |
| unassigned department | 1,537 | **1** |
| top department | *Unassigned* | Mathematics (15) |

Department breakdown is now meaningful: Mathematics 15, Nursing 14, Social Work 12,
Geography and Geosciences 9, English 9, Management 9.

`admin/stats/` keeps the full row count for administration but now also reports
`su_affiliated: 184` and `external_coauthors: 1537`, and its department breakdown uses the
SU-scoped queryset.

- **Verification:** `manage.py check` clean; public dataset 184 faculty / 9,237 papers;
  admin stats returns the new split.
- **Status:** In repo + repo DB. **NOT yet deployed.**

### 2026-08-29 11:18 - Stale denormalized faculty metrics recomputed

- **Restore ID:** `DB-20260829-1118`
- **Artifact:** `~/scoup-backups/db.sqlite3.pre-recalc.*`
- **File added:** `academic/management/commands/recalc_faculty_metrics.py`

**Reported symptom:** searching Computer Science showed Enyue Lu with 29 papers, but her faculty
card showed 1.

**Cause - the card was wrong, not the search.** `article_count`, `total_citations` and
`average_citations` are *denormalized fields* written by the original import. Ingesting 9,237
OpenAlex works created the `authors` links but never refreshed those stored numbers, so they
drifted. Enyue Lu had `article_count=1` while **31** papers were linked to her.

This was systemic, not a one-off: **163 of 182** directory-verified faculty (and 1,553 of 1,721
rows overall) had `article_count` disagreeing with their linked papers.

`recalc_faculty_metrics` recomputes all three fields from the actual `papers` relation.
Dry-run by default; `--apply` writes; `--only-affiliated` limits to SU records.

| | Before | After |
| --- | --- | --- |
| Enyue Lu article_count | 1 | **31** |
| Enyue Lu total_citations | 0 | **119** |
| Enyue Lu average_citations | 0.0 | **3.84** |
| verified faculty mismatched | 163 | **0** |

1,553 records updated.

**Why this recurs:** any ingest that adds papers invalidates these fields. The command is
idempotent and cheap, so it is the natural first job for the scheduled validation worker -
it should run after every OpenAlex import.

- **Verification:** `manage.py check` clean; zero verified faculty mismatched after the run.
- **Status:** In repo + repo DB. **NOT yet deployed.**

### 2026-08-29 12:49 - Scheduled validation worker

- **Restore ID:** `SRC-20260829-1249`
- **Artifact:** `~/scoup-backups/db.sqlite3.pre-worker.*`
- **Files added:** `academic/management/commands/run_validation.py`,
  `deploy/scoup-validation.service`, `deploy/scoup-validation.timer`

Replaces manual, credit-consuming inspection with a nightly job. Runs the ingest and repair
commands **in dependency order** - new papers must land before metrics are recomputed from them,
which is exactly the ordering whose absence caused the Enyue Lu drift.

| Job | Command | Purpose |
| --- | --- | --- |
| openalex | `import_openalex` | pull new works since the last run |
| metrics | `recalc_faculty_metrics` | repair denormalized counts |
| schools | `import_su_schools` | resolve schools for new departments |

**Repair vs. report is a deliberate split.** Jobs fix only what is safely derivable from data
already present. The audit pass *reports* and never writes, because guessing is what produced the
bad data the worker exists to catch (see the *Shing Yip Lee -> Physics* false positive).

Audit baseline:

| Signal | Value |
| --- | --- |
| faculty SU-affiliated / external | 184 / 1,537 |
| metric drift | 0 |
| pending review | 128 |
| affiliated missing school | 17 |
| papers total | 9,237 |
| papers without linked authors | 6,062 |
| papers without abstract | 3,630 |
| unreviewed inquiries | 0 |

**Safety:** dry-run by default (`--apply` writes); `flock` prevents overlapping runs; a failing
job is captured and does not abort the rest; incremental runs overlap the window by one day
because OpenAlex backdates indexing. `--full`, `--audit-only`, `--jobs`, `--json` supported.
The systemd unit runs as **`User=rellis`** (matching Gunicorn - running as `www-data` caused the
10:18 outage) with `ProtectSystem=full` and a single `ReadWritePaths`.

**Two bugs found and fixed during verification, both silent:**

1. The audit queried `Paper.faculty_members`, which **resolved without error** and returned
   `papers_without_authors: 0`. The real field is `authors`; the true figure is **6,062**. A
   wrong-but-plausible zero in a monitoring tool is worse than a crash.
2. `settings.BASE_DIR` points at the *settings package* (`scoup-backend/scoupdb`), not the repo
   root, so run state was written outside the project. Anchored to `Path(__file__).parents[3]`
   after printing each depth rather than assuming.

**Install:**

```bash
sudo cp deploy/scoup-validation.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now scoup-validation.timer
systemctl list-timers scoup-validation.timer
journalctl -u scoup-validation.service -n 50
```

- **Verification:** `manage.py check` clean; audit-only, per-job and full `--apply` runs all
  succeed (78s end to end); state file persists and drives the next incremental window.
- **Status:** In repo. Timer **not yet installed** on the server.

### 2026-08-29 12:49 - Admin dashboard credentials located

- **Restore ID:** `NONE` (credential change only)
- **Finding:** the admin dashboard was **already built** - it was never missing, only unreachable.

`scoup-frontend-2.0/src/components/` contains `AdminLogin.tsx`, `AdminDashboard.tsx` and twelve
pages under `admin/` totalling ~6,000 lines: Overview, FacultyManagement, PendingApprovals,
Inquiries, DepartmentManagement, PlatformAnalytics, StrategicInsights, AdminAnalytics, Messages,
Profile, SystemSettings, ContactPageEditor. These pair with the eleven `/api/admin/*` endpoints
built on 2026-08-28.

**Credential state.** Both databases hold the same 5 users; 3 are superusers (`rellis`, `ryan`,
`opeade`). None matched `ScoupAdmin123!` from `ensure_superuser.py`, so the seeded password had
been changed and is unrecoverable by design (PBKDF2). Reset `rellis` interactively via
`manage.py changepassword` on the live DB - the password was typed directly into the terminal and
never passed through the assistant.

**Login path clarified.** `AdminLogin.tsx` posts to **`/api/token/`** (SimpleJWT,
`EmailOrUsernameTokenObtainPairView`, accepting email *or* username), not `/api/auth/login/`.
Confirmed live: `/api/auth/login/` -> 404, `/api/admin/me/` -> 401 anonymous (correct).

Sign in at `https://scoup-salisbury.net/admin-login` as `rellis`; `App.tsx` calls `adminMe()`
and routes to `/admin-dashboard` on success.

- **Status:** Live password updated. Dashboard awaiting your sign-in to validate.

### 2026-08-29 13:56 - Search word-boundary bug fixed (deployed)

- **Restore ID:** `SRC-20260829-1356`
- **File changed:** `academic/views.py` (`_word_match`)
- **Commit:** `aa8b3ca` (applied by Claude Code, running in parallel per the user's
  request to keep work moving when this assistant reaches its limit)

**Reported symptom:** searching "AI" returned "Method for Comparing Concentrations of the
Open-Air Factor" (1973, Porton Down, England) as a **100% match**, alongside other papers
whose author affiliations say "Salisbury" (the English city) rather than "Salisbury University".

**Root cause found - not an affiliation/data problem, a regex bug.** `_word_match()` built its
pattern as `r"\b" + re.escape(token)`, with **no trailing `\b`**. A leading-boundary-only
pattern for `"ai"` matches the start of `"air"` (bounded by the hyphen in `"open-air"`) and never
checks that the match also ends at a word boundary, so `"AI"` silently matched inside `"Air"`,
`"CS"` inside `"cost"`, etc., for every short (2-3 letter) query term. This inflated confidence to
100 for completely unrelated papers, which is what made the false result look authoritative.

Fix: `r"\b" + re.escape(token) + r"\b"`.

| Query vs. text | Before | After |
| --- | --- | --- |
| "ai" vs "open-air factor" | **True (bug)** | False |
| "cs" vs "physics course" | **True (bug)** | False |
| "ai" vs "artificial intelligence (ai)" | True | True |

**On the underlying data.** The Open-Air Factor paper (and ~2,836 other pre-1990 records) come
from the original legacy dataset (`import_full_dataset`), which has **no institution filter at
all** - unlike `import_openalex`, which scopes to OpenAlex institution `I9364636`. These records
have empty `faculty_affiliations` and no linked `authors`, so they were never counted toward
faculty metrics, but they do sit in the searchable corpus. This is the same population already
tracked as **item #14** (6,062 papers with no linked author) - the word-boundary bug is what let
them surface with a misleadingly high score; the presence of off-topic legacy records in the
corpus is a separate, still-open data-quality issue.

- **Verification:** unit-level regex tests pass; deployed to live and confirmed -
  `GET /api/search/?q=AI` no longer returns the Open-Air Factor paper; top results are genuine
  AI papers (conversational agents, ML-based HR systems) scoring 94.4 / 89.x.
- **Status:** Deployed to `/var/www` and restarted. Code-only change; no migration required.

### 2026-08-29 14:03 - Superuser cleanup

- **Restore ID:** `DB-20260829-1401`
- **Artifact:** `~/scoup-backups/db.sqlite3.pre-userscleanup.*`

Removed all users except `rellis` from both the repo and live databases, at the user's request.

| Username | Reason removed |
| --- | --- |
| `ryan` | Seed superuser created by `ensure_superuser.py`; never logged in |
| `opeade` | Superuser with real login history (2025-11-17) - user confirmed removal |
| `opefaculty` | Non-staff test account; cascaded 1 linked Faculty row |
| `tife22` | Non-staff test account; cascaded 1 linked Faculty row |

`ensure_superuser.py` is not invoked by any deploy step or systemd unit, so `ryan` will not be
silently recreated. `rellis` is now the sole user in both databases.

- **Status:** Applied to repo DB and live DB.

### 2026-08-29 14:33 - Live deploy caught up; validation timer installed

- **Restore ID:** `DB-20260829-1433`
- **Artifact:** `~/scoup-backups/varwww-db.sqlite3.pre-recalc.*`

Copied `recalc_faculty_metrics.py` / `run_validation.py` to live (only files missing vs. repo -
`views.py` and migrations were already current). Ran `recalc_faculty_metrics --apply` on live:
**1,466 records corrected**. Papers were already 9,237 on both sides, so the OpenAlex backfill
needed no further action. The 87 dual-source faculty from the 11:03 entry were created by an
ad-hoc script, not a management command, so they still only exist in the repo DB - noted as a
gap, not reproduced here to conserve time.

Installed `scoup-validation.timer` on the live host (`systemctl enable --now`) - next run
tomorrow 03:30-03:45.

- **Status:** Live DB metrics current. Worker active on schedule.

---

## Known open items

| # | Item | Severity | Status |
| --- | --- | --- | --- |
| 3 | 128 faculty pending review; 1,537 external co-authors now excluded from public metrics | Medium | Partly resolved |
| 4 | ~40 frontend endpoints still missing (faculty portal, OTP auth, tickets, AI generation) | Medium | Open |
| 5 | Six sidebar pages still placeholders (Search, Networks, Projects, Labs, Facilities, Institutions) | Medium | Open |
| 6 | Labs have no data source; SU research pages list none | Medium | Open |
| 7 | Facility building codes captured (105) but not yet joined to faculty rooms | Low | Open |
| 8 | Institutions extraction has noise (name bleed, truncated multi-part names) | Low | Open |
| 9 | `patentsData` / `projectsData` are empty, so those panels render zeros | Low | Open |
| 10 | Frontend bundle is 1.1 MB; needs code-splitting | Low | Open |
| 11 | `/var/www/.../templates` is `drwxr-x--- root root`, unreadable by the service | Low | Open |
| 12 | No **public** faculty profile page; only the authenticated self-service dashboard exists | Medium | Open |
| 13 | Validation worker built; systemd timer not yet installed on the server | Medium | Partly resolved |
| 14 | 6,062 of 9,237 papers have no linked author (legacy dataset has no institution filter); they cannot surface on a profile and pollute search/category corpus | High | Open |
| 15 | 3,630 papers have no abstract, weakening search recall | Medium | Open |

**Resolved:** full OpenAlex backfill, category granularity, DEBUG exposure, categories stub, search relevance, repo/deploy divergence,
DB-overwriting deploy, `/var/www` git remote, broken `backupAll.sh` refs, deploy outage.
