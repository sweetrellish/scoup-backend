"""Shared SU-directory matching logic and admin review evidence.

`import_su_directory` decides whether a Faculty row can be auto-verified from
`SUdirectory.pdf`. When it cannot, the row is parked at `review_status='pending'`
with nothing recorded about *why* - which left the admin queue unreviewable.

This module recomputes that decision on demand so the queue can show an admin the
same evidence the importer saw: which directory rows share the surname, which one
it would have picked, and what made the match too weak to trust.

The PDF takes ~20s to parse, so matching reads a cached JSON export written by
`manage.py export_su_directory`. The cache is memoized per process.
"""

import json
import re
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIRECTORY_CACHE = REPO_ROOT / "data" / "su_directory.json"
BUILDING_CODES = REPO_ROOT / "data" / "su_building_codes.json"

_lock = threading.Lock()
_cache = {}


def normalize(value):
    """Fold to comparable letters only - matches import_su_directory."""
    return re.sub(r"[^a-z]", "", (value or "").lower())


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def load_directory():
    """Directory rows grouped by normalized surname. Empty if the cache is absent."""
    with _lock:
        if "rows" not in _cache:
            rows = _load_json(DIRECTORY_CACHE, [])
            by_last = {}
            for row in rows:
                by_last.setdefault(normalize(row.get("last_name")), []).append(row)
            _cache["rows"] = rows
            _cache["by_last"] = by_last
        return _cache["rows"], _cache["by_last"]


def load_building_codes():
    with _lock:
        if "buildings" not in _cache:
            _cache["buildings"] = _load_json(BUILDING_CODES, {})
        return _cache["buildings"]


def reset_cache():
    """Drop memoized data - used by the export command after rewriting the cache."""
    with _lock:
        _cache.clear()


def building_for_room(room):
    """'AC262' -> 'Academic Commons'. Returns '' when the prefix is unknown.

    Only the documented two-character codes are resolved; an unrecognised prefix
    yields nothing rather than a guessed building.
    """
    code = re.match(r"^\s*([A-Za-z]{1,3})", room or "")
    if not code:
        return ""
    return load_building_codes().get(code.group(1).upper(), "")


def split_faculty_name(faculty):
    """(first, last) normalized, falling back to the combined `name` field."""
    last = normalize(faculty.last_name)
    first = normalize(faculty.first_name)
    if not last and faculty.name:
        parts = faculty.name.split()
        last = normalize(parts[-1])
        first = normalize(parts[0]) if len(parts) > 1 else ""
    return first, last


def _serialize_row(row):
    room = row.get("room") or ""
    return {
        "last_name": row.get("last_name") or "",
        "first_name": row.get("first_name") or "",
        "title": row.get("title") or "",
        "department": row.get("department") or "",
        "room": room,
        "building": building_for_room(room),
        "phone_ext": row.get("phone_ext") or "",
    }


def match_directory(first, last):
    """Replicate the importer's decision and explain it.

    Returns `match_type` in:
      exact           - one directory row with the same full first name (auto-verified)
      initial         - exactly one row shares the first initial (review required)
      ambiguous       - several rows share the surname, none resolvable without guessing
      no_first_name   - surname matched but the Faculty row has no first name to compare
      unmatched       - no directory row carries this surname
      no_directory    - the directory cache has not been exported yet
    """
    rows, by_last = load_directory()
    if not rows:
        return {
            "match_type": "no_directory",
            "reason": (
                "The SU directory cache is missing. Run "
                "`manage.py export_su_directory --apply` to enable match evidence."
            ),
            "candidates": [],
            "best_match": None,
        }

    candidates = by_last.get(last, [])
    if not candidates:
        return {
            "match_type": "unmatched",
            "reason": "No row in the SU directory has this surname.",
            "candidates": [],
            "best_match": None,
        }

    serialized = [_serialize_row(row) for row in candidates]
    exact = [c for c in serialized if normalize(c["first_name"]) == first]

    if len(exact) == 1:
        return {
            "match_type": "exact",
            "reason": "Full first name and surname match exactly one directory row.",
            "candidates": serialized,
            "best_match": exact[0],
        }
    if len(exact) > 1:
        return {
            "match_type": "ambiguous",
            "reason": (
                f"{len(exact)} directory rows share this exact first and last name, "
                "so the correct person cannot be determined from the directory alone."
            ),
            "candidates": exact,
            "best_match": None,
        }

    if not first:
        return {
            "match_type": "no_first_name",
            "reason": (
                "This record has no first name, so it cannot be distinguished from the "
                f"{len(serialized)} directory row(s) with this surname."
            ),
            "candidates": serialized,
            "best_match": None,
        }

    initial = [c for c in serialized if normalize(c["first_name"])[:1] == first[:1]]
    if len(initial) == 1:
        return {
            "match_type": "initial",
            "reason": (
                f"Only the first initial matched: the directory lists "
                f"\"{initial[0]['first_name']} {initial[0]['last_name']}\" but this record "
                f"reads \"{first[:1].upper()}... {last.title()}\". A shared initial is not "
                "proof of identity - approving asserts these are the same person."
            ),
            "candidates": serialized,
            "best_match": initial[0],
        }
    if len(initial) > 1:
        return {
            "match_type": "ambiguous",
            "reason": (
                f"{len(initial)} directory rows share this surname and first initial, "
                "so no single row can be selected without guessing."
            ),
            "candidates": initial,
            "best_match": None,
        }

    return {
        "match_type": "unmatched",
        "reason": (
            f"{len(serialized)} directory row(s) share the surname, but none share the "
            "first initial."
        ),
        "candidates": serialized,
        "best_match": None,
    }


def review_evidence(faculty, paper_titles=None):
    """Full evidence bundle for one Faculty row in the admin review queue."""
    first, last = split_faculty_name(faculty)
    result = match_directory(first, last)
    result["searched_for"] = {
        "first_name": (faculty.first_name or "").strip(),
        "last_name": (faculty.last_name or "").strip(),
        "name": (faculty.name or "").strip(),
    }
    result["signals"] = {
        "article_count": faculty.article_count or 0,
        "total_citations": faculty.total_citations or 0,
        "orcid": faculty.orcid or "",
        "openalex_id": faculty.openalex_id or "",
        "has_login": bool(faculty.user_id),
        "directory_verified": bool(faculty.directory_verified),
    }
    if paper_titles is not None:
        result["recent_papers"] = paper_titles
    return result
