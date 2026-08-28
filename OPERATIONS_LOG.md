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

---

## Known open items

| # | Item | Severity | Status |
| --- | --- | --- | --- |
| 1 | `DEBUG` resolves to `True` in production; tracebacks are publicly exposed | High (security) | Open |
| 2 | `/api/categories/` still returns hardcoded stub `["CS", "Bio"]` | High | Open |
| 3 | Search relevance is unweighted substring matching; returns irrelevant results | High | Open |
| 4 | `/var/www` backend and `/home/rellis/scoup-backend` have diverged | Medium | Open |
| 5 | `/var/www/.../scoup-backend/templates` is `drwxr-x--- root root`, unreadable by the service | Low | Open |
