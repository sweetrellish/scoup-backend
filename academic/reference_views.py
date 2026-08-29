"""Read-only reference endpoints: institutions and campus facilities.

Both are backed by curated JSON in `data/`, not by database tables, because
neither has a maintained model yet. Files are memoized per process.

Institutions come from `data/institutions_clean.json`, produced by
`manage.py clean_institutions` from the raw affiliation extraction. Facilities
join the campus building-code list to the facilities page listing, and to
faculty room numbers, using exact matches only.
"""

import json
import re
import threading
from collections import defaultdict
from pathlib import Path

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .directory_match import resolve_school
from .models import Faculty
from .views import _su_affiliated

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

_lock = threading.Lock()
_cache = {}


def _load(filename, default):
    with _lock:
        if filename not in _cache:
            try:
                _cache[filename] = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _cache[filename] = default
        return _cache[filename]


@api_view(["GET"])
@permission_classes([AllowAny])
def institutions_list(request):
    """Institutions appearing in the publication corpus.

    `mentions` counts occurrences in paper affiliation text, so it measures how
    often an institution appears alongside SU research - not a partnership
    agreement, and not a headcount. The response says so explicitly rather than
    letting the number be read as something it is not.
    """
    entries = _load("institutions_clean.json", [])

    query = (request.query_params.get("q") or "").strip().lower()
    if query:
        entries = [e for e in entries if query in e["name"].lower()]

    if (request.query_params.get("exclude_host") or "").lower() == "true":
        entries = [e for e in entries if not e.get("is_host")]

    try:
        limit = max(1, min(int(request.query_params.get("limit", 200)), 500))
    except (TypeError, ValueError):
        limit = 200

    visible = entries[:limit]
    return Response(
        {
            "count": len(visible),
            "total": len(entries),
            "results": [
                {
                    "name": e["name"],
                    "mentions": e["mentions"],
                    "isHost": bool(e.get("is_host")),
                    # Raw extraction variants folded into this entry, so a reviewer
                    # can see exactly what was merged rather than trusting the total.
                    "mergedFrom": e.get("merged_from", []),
                }
                for e in visible
            ],
            "source": {
                "derivedFrom": "affiliation text in the publication corpus",
                "metric": "mentions - occurrences in affiliation text, not partnership status",
                "cleaning": (
                    "Name bleed and sentence bleed were stripped; truncated names that "
                    "could not be resolved without guessing were dropped."
                ),
            },
        },
        status=status.HTTP_200_OK,
    )


def _room_prefix(room):
    match = re.match(r"^\s*([A-Za-z]{1,3})", room or "")
    return match.group(1).upper() if match else ""


@api_view(["GET"])
@permission_classes([AllowAny])
def facilities_list(request):
    """Campus buildings, joined to the faculty who have a room in them.

    Three sources are combined on **exact** name/code matches only:
      - `data/su_building_codes.json` - 105 codes from the campus building-info page
      - `data/su_facilities.json`     - 37 buildings listed on the facilities page
      - `Faculty.room`                - room prefixes from the SU directory import

    Near-misses are deliberately not reconciled: the facilities page says
    "Devilbiss Hall" where the code list says "Devilbiss Science Hall", and
    deciding those are the same building is a guess. Each entry reports which
    sources it appeared in so the gap is visible instead of papered over.
    """
    codes = _load("su_building_codes.json", {})
    listed = _load("su_facilities.json", [])

    # Several codes map to one building (Dogwood Village has 16), so group by name.
    by_name = defaultdict(list)
    for code, name in codes.items():
        by_name[name].append(code)

    occupants = defaultdict(list)
    for member in Faculty.objects.filter(_su_affiliated()).exclude(room__isnull=True).exclude(room=""):
        building = codes.get(_room_prefix(member.room))
        if building:
            occupants[building].append(member)

    listed_set = set(listed)
    entries = []

    for name, building_codes in by_name.items():
        people = occupants.get(name, [])
        departments = sorted({(m.department or "").strip() for m in people if (m.department or "").strip()})
        entries.append(
            {
                "name": name,
                "codes": sorted(building_codes),
                "facultyCount": len(people),
                "departments": departments,
                "schools": sorted({s for s in (resolve_school(d) for d in departments) if s}),
                "onFacilitiesPage": name in listed_set,
                "hasBuildingCode": True,
            }
        )

    # Buildings named on the facilities page that no code resolves to.
    for name in listed:
        if name not in by_name:
            entries.append(
                {
                    "name": name,
                    "codes": [],
                    "facultyCount": 0,
                    "departments": [],
                    "schools": [],
                    "onFacilitiesPage": True,
                    "hasBuildingCode": False,
                }
            )

    query = (request.query_params.get("q") or "").strip().lower()
    if query:
        entries = [
            e
            for e in entries
            if query in e["name"].lower()
            or any(query in d.lower() for d in e["departments"])
            or any(query == c.lower() for c in e["codes"])
        ]

    if (request.query_params.get("occupied") or "").lower() == "true":
        entries = [e for e in entries if e["facultyCount"] > 0]

    entries.sort(key=lambda e: (-e["facultyCount"], e["name"]))

    return Response(
        {
            "count": len(entries),
            "results": entries,
            "summary": {
                "buildingsWithCodes": len(by_name),
                "buildingCodes": len(codes),
                "onFacilitiesPage": len(listed),
                "occupiedByFaculty": sum(1 for e in entries if e["facultyCount"] > 0),
                "facultyPlaced": sum(e["facultyCount"] for e in entries),
            },
            "source": {
                "join": "exact building-code and name matches only; near-misses are not merged",
                "note": (
                    "Faculty placement comes from room numbers in the SU directory, so it "
                    "covers only faculty whose directory row was matched."
                ),
            },
        },
        status=status.HTTP_200_OK,
    )
