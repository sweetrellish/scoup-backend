"""Admin review queue for papers restored from the 2026-08-30 purge.

Mirrors the Faculty review pattern (admin_views.py: admin_faculty_list/approve/
reject/bulk_action) exactly, so the frontend can reuse the same UI. A paper
only ever reaches review_status='pending' by being restored here from a purge -
nothing in the normal import pipeline creates a pending paper, so approving one
is a deliberate, evidenced decision, not routine data entry.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAdminUser
from rest_framework.response import Response

from academic.models import Paper


class IsSuperUser(BasePermission):
    """5,982 unverified papers is a personal review queue, not a general staff tool."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


def _serialize_admin_paper(paper):
    # `faculty_members` is a denormalised list of *author name strings* copied
    # from the source record. It is NOT evidence of an SU affiliation - every one
    # of the 5,982 restored papers has a non-empty faculty_members and zero rows
    # in the authors M2M. `linked_faculty` is the real signal: the SU Faculty
    # profiles actually joined to this paper, which is what the purge tested.
    return {
        "id": paper.pk,
        "doi": paper.doi,
        "title": paper.title,
        "journal": paper.journal or "",
        "abstract": (paper.abstract or "")[:500],
        "year": paper.date_published.year if paper.date_published else None,
        "citations": paper.tc_count,
        "keywords": (paper.keywords or [])[:15],
        "faculty_members": paper.faculty_members or [],
        "linked_faculty": [
            {"id": f.pk, "name": f.name, "department": f.department or ""}
            for f in paper.authors.all()
        ],
        "review_status": paper.review_status,
        "review_note": paper.review_note,
        "url": paper.url or "",
    }


@api_view(["GET"])
@permission_classes([IsSuperUser])
def admin_paper_list(request):
    qs = Paper.objects.all().prefetch_related("authors")

    search = (request.query_params.get("search") or "").strip()
    if search:
        qs = qs.filter(title__icontains=search)

    state = (request.query_params.get("status") or "pending").strip().lower()
    if state in {"pending", "approved", "rejected"}:
        qs = qs.filter(review_status=state)

    try:
        limit = max(1, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    total = qs.count()
    papers = list(qs.order_by("-date_published", "-tc_count")[:limit])

    return Response(
        {"count": total, "results": [_serialize_admin_paper(p) for p in papers]},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsSuperUser])
def admin_paper_approve(request, pk):
    paper = Paper.objects.filter(pk=pk).first()
    if not paper:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    paper.review_status = "approved"
    paper.review_note = ""
    paper.save(update_fields=["review_status", "review_note"])
    return Response(_serialize_admin_paper(paper), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsSuperUser])
def admin_paper_reject(request, pk):
    paper = Paper.objects.filter(pk=pk).first()
    if not paper:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    reason = str((request.data or {}).get("reason") or "")[:1000]
    paper.review_status = "rejected"
    paper.review_note = reason
    paper.save(update_fields=["review_status", "review_note"])
    return Response(_serialize_admin_paper(paper), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsSuperUser])
def admin_paper_bulk_action(request):
    data = request.data if isinstance(request.data, dict) else {}
    action = str(data.get("action") or "").strip().lower()
    ids = data.get("ids") or []

    if action not in {"approve", "reject"}:
        return Response(
            {"detail": "action must be 'approve' or 'reject'."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(ids, list) or not ids:
        return Response({"detail": "ids must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)

    reason = str(data.get("reason") or "")[:1000]
    new_status = "approved" if action == "approve" else "rejected"

    updated = 0
    for paper in Paper.objects.filter(pk__in=[i for i in ids if str(i).isdigit()]):
        paper.review_status = new_status
        paper.review_note = "" if new_status == "approved" else reason
        paper.save(update_fields=["review_status", "review_note"])
        updated += 1

    return Response({"updated": updated, "action": action}, status=status.HTTP_200_OK)
