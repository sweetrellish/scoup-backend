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

---

## Known open items

| # | Item | Severity | Status |
| --- | --- | --- | --- |
| 1 | `DEBUG` resolves to `True` in production; tracebacks are publicly exposed | High (security) | Open |
| 2 | `/api/categories/` still returns hardcoded stub `["CS", "Bio"]` | High | Open |
| 3 | Search relevance is unweighted substring matching; returns irrelevant results | High | Open |
| 4 | `/var/www` backend and `/home/rellis/scoup-backend` have diverged | Medium | Open |
| 5 | `/var/www/.../scoup-backend/templates` is `drwxr-x--- root root`, unreadable by the service | Low | Open |
