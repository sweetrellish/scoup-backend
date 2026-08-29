"""Admin endpoints: faculty validation, platform stats, and inquiry triage."""

import logging
from collections import Counter

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import Faculty, NetworkInquiry, Paper, Patent, Project
from .views import _full_name, _normalize_keyword_list, _su_affiliated

logger = logging.getLogger(__name__)


def _serialize_admin_faculty(member):
    user = member.user
    return {
        "id": member.pk,
        "name": _full_name(
            member.first_name,
            member.last_name,
            (member.name or "").strip() or member.faculty_id,
        ),
        "email": member.email or "",
        "institutional_email": member.institutional_email or "",
        "institutional_email_verified": bool(member.institutional_email_verified),
        "has_user": bool(user),
        "date_joined": user.date_joined.isoformat() if user else None,
        "last_login": user.last_login.isoformat() if user and user.last_login else None,
        "last_active": member.last_active.isoformat() if member.last_active else None,
        "primary_department": member.department or "",
        "departments": _normalize_keyword_list(member.department_affiliations)
        or ([member.department] if member.department else []),
        "title": member.title or "",
        "is_approved": bool(member.is_approved),
        "profile_visibility": bool(member.profile_visibility),
        "review_status": member.review_status,
        "review_note": member.review_note or "",
        "directory_verified": bool(member.directory_verified),
        "article_count": member.article_count or 0,
        "total_citations": member.total_citations or 0,
        "created_at": member.created_at.isoformat(),
        "updated_at": member.updated_at.isoformat(),
    }


@api_view(["GET", "PATCH"])
@permission_classes([IsAdminUser])
def admin_me(request):
    user = request.user
    if request.method == "PATCH":
        data = request.data if isinstance(request.data, dict) else {}
        for field in ("first_name", "last_name", "email"):
            if field in data:
                setattr(user, field, str(data[field])[:254])
        user.save(update_fields=["first_name", "last_name", "email"])

    return Response(
        {
            "id": user.pk,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_stats(request):
    faculty_qs = Faculty.objects.all()
    approved = faculty_qs.filter(review_status="approved").count()
    rejected = faculty_qs.filter(review_status="rejected").count()
    pending = faculty_qs.filter(review_status="pending").count()

    su_qs = faculty_qs.filter(_su_affiliated())
    dept_counts = Counter(
        (f or "").strip()
        for f in su_qs.values_list("department", flat=True)
        if (f or "").strip()
    )

    return Response(
        {
            "faculty": {
                "total": faculty_qs.count(),
                "approved": approved,
                "pending": pending,
                "rejected": rejected,
                "unverified": faculty_qs.filter(directory_verified=False).count(),
                "hidden": faculty_qs.filter(
                    is_approved=True, profile_visibility=False
                ).count(),
                "directory_verified": faculty_qs.filter(directory_verified=True).count(),
                # Rows also include external co-authors kept for paper attribution.
                "su_affiliated": su_qs.count(),
                "external_coauthors": faculty_qs.count() - su_qs.count(),
            },
            "content": {
                "papers": Paper.objects.count(),
                "patents": Patent.objects.count(),
                "projects": Project.objects.count(),
            },
            "inquiries": {
                "total": NetworkInquiry.objects.count(),
                "new": NetworkInquiry.objects.filter(status="new").count(),
                "reviewed": NetworkInquiry.objects.filter(status="reviewed").count(),
                "closed": NetworkInquiry.objects.filter(status="closed").count(),
            },
            "department_breakdown": [
                {"department": name, "faculty_count": count}
                for name, count in dept_counts.most_common(25)
            ],
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_faculty_list(request):
    qs = Faculty.objects.select_related("user").all()

    search = (request.query_params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(department__icontains=search)
        )

    department = (request.query_params.get("department") or "").strip()
    if department:
        qs = qs.filter(department__iexact=department)

    if (request.query_params.get("pending") or "").lower() == "true":
        qs = qs.filter(review_status="pending")

    state = (request.query_params.get("status") or "").strip().lower()
    if state in {"pending", "approved", "rejected"}:
        qs = qs.filter(review_status=state)
    elif state == "verified":
        qs = qs.filter(directory_verified=True)
    elif state == "unverified":
        qs = qs.filter(directory_verified=False)

    try:
        limit = max(1, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    return Response(
        [_serialize_admin_faculty(m) for m in qs.order_by("last_name", "first_name")[:limit]],
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAdminUser])
def admin_faculty_detail(request, pk):
    member = Faculty.objects.filter(pk=pk).select_related("user").first()
    if not member:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        data = request.data if isinstance(request.data, dict) else {}
        editable = {
            "title", "department", "email", "office", "room", "phone", "bio",
            "profile_visibility", "is_approved", "review_status", "review_note",
            "institutional_email", "institutional_email_verified", "directory_verified",
        }
        changed = []
        for field, value in data.items():
            if field in editable:
                setattr(member, field, value)
                changed.append(field)
        if changed:
            member.save(update_fields=changed + ["updated_at"])

    return Response(_serialize_admin_faculty(member), status=status.HTTP_200_OK)


def _set_review(member, new_status, reason, reviewer):
    member.review_status = new_status
    member.is_approved = new_status == "approved"
    if new_status == "rejected":
        member.profile_visibility = False
    if reason:
        member.review_note = f"{reason} (by {reviewer})"[:2000]
    member.save(
        update_fields=[
            "review_status", "is_approved", "profile_visibility", "review_note", "updated_at",
        ]
    )


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_faculty_approve(request, pk):
    member = Faculty.objects.filter(pk=pk).first()
    if not member:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    _set_review(member, "approved", "", request.user.get_username())
    return Response(_serialize_admin_faculty(member), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_faculty_reject(request, pk):
    member = Faculty.objects.filter(pk=pk).first()
    if not member:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    reason = str((request.data or {}).get("reason") or "")[:1000]
    _set_review(member, "rejected", reason, request.user.get_username())
    return Response(_serialize_admin_faculty(member), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_faculty_bulk_action(request):
    data = request.data if isinstance(request.data, dict) else {}
    action = str(data.get("action") or "").strip().lower()
    ids = data.get("ids") or []

    if action not in {"approve", "reject"}:
        return Response(
            {"detail": "action must be 'approve' or 'reject'."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(ids, list) or not ids:
        return Response({"detail": "ids must be a non-empty list."},
                        status=status.HTTP_400_BAD_REQUEST)

    reason = str(data.get("reason") or "")[:1000]
    reviewer = request.user.get_username()
    new_status = "approved" if action == "approve" else "rejected"

    updated = 0
    for member in Faculty.objects.filter(pk__in=[i for i in ids if str(i).isdigit()]):
        _set_review(member, new_status, reason, reviewer)
        updated += 1

    return Response({"updated": updated, "action": action}, status=status.HTTP_200_OK)


def _serialize_admin_inquiry(inquiry):
    return {
        "id": inquiry.pk,
        "target_faculty_name": inquiry.target_faculty_name,
        "target_department": inquiry.target_department,
        "target_school": inquiry.target_school,
        "target_project_title": inquiry.target_project_title,
        "from_faculty_name": inquiry.requester_name,
        "from_faculty_email": inquiry.requester_email,
        "from_faculty_department": inquiry.requester_organization,
        "requester_role": inquiry.requester_role,
        "shared_keywords": inquiry.shared_keywords or [],
        "note": inquiry.note,
        "message_subject": inquiry.message_subject,
        "source_type": inquiry.source_type or "public",
        "status": inquiry.status,
        "admin_notes": inquiry.admin_notes,
        "reviewed_by": inquiry.reviewed_by,
        "created_at": inquiry.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_inquiries(request):
    qs = NetworkInquiry.objects.all()
    state = (request.query_params.get("status") or "").strip().lower()
    if state in {"new", "reviewed", "closed"}:
        qs = qs.filter(status=state)
    source = (request.query_params.get("source_type") or "").strip()
    if source:
        qs = qs.filter(source_type=source)
    return Response([_serialize_admin_inquiry(i) for i in qs[:500]], status=status.HTTP_200_OK)


@api_view(["PATCH"])
@permission_classes([IsAdminUser])
def admin_inquiry_detail(request, pk):
    inquiry = NetworkInquiry.objects.filter(pk=pk).first()
    if not inquiry:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data if isinstance(request.data, dict) else {}
    changed = []
    if "status" in data:
        new_status = str(data["status"]).strip().lower()
        if new_status not in {"new", "reviewed", "closed"}:
            return Response(
                {"detail": "status must be new, reviewed, or closed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        inquiry.status = new_status
        inquiry.reviewed_by = request.user.get_username()
        changed += ["status", "reviewed_by"]
    if "admin_notes" in data:
        inquiry.admin_notes = str(data["admin_notes"])[:4000]
        changed.append("admin_notes")

    if changed:
        inquiry.save(update_fields=changed + ["updated_at"])
    return Response(_serialize_admin_inquiry(inquiry), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_audit_log(request):
    """Recent review activity, derived from stored review state."""
    entries = []
    for member in Faculty.objects.exclude(review_status="pending").order_by("-updated_at")[:100]:
        entries.append(
            {
                "id": member.pk,
                "type": "faculty_review",
                "actor": "",
                "summary": f"{_full_name(member.first_name, member.last_name, member.name or '')} "
                f"marked {member.review_status}",
                "detail": member.review_note or "",
                "timestamp": member.updated_at.isoformat(),
            }
        )
    return Response(entries, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_faculty_message(request, pk):
    member = Faculty.objects.filter(pk=pk).first()
    if not member:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data if isinstance(request.data, dict) else {}
    note = str(data.get("note") or "").strip()
    if not note:
        return Response({"detail": "note is required."}, status=status.HTTP_400_BAD_REQUEST)

    inquiry = NetworkInquiry.objects.create(
        target_faculty=member,
        target_faculty_name=_full_name(
            member.first_name, member.last_name, member.name or member.faculty_id
        ),
        target_department=member.department or "",
        requester=request.user,
        requester_name=request.user.get_username(),
        requester_email=request.user.email or "",
        note=note[:4000],
        message_subject=str(data.get("message_subject") or "")[:255],
        source_type="admin",
        reviewed_by=request.user.get_username(),
    )
    return Response(
        {"id": inquiry.pk, "detail": "Message recorded."}, status=status.HTTP_201_CREATED
    )
