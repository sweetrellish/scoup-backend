"""Network discovery and inquiry endpoints.

Kept separate from views.py so the discovery/inquiry surface stays self-contained.
"""

import logging
from collections import Counter

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Faculty, NetworkInquiry, Paper, Patent, Project
from .views import (
    _full_name,
    _normalize_keyword_list,
    _visible_faculty_qs,
    _year_from_dates,
)

logger = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 4000
PUBLIC_INQUIRY_HOURLY_LIMIT = 5


def _seed_keywords(request, query):
    """Terms driving discovery: explicit query, else the signed-in profile."""
    if query:
        return [query.lower()]

    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        faculty = Faculty.objects.filter(user=user).first()
        if faculty:
            merged = (
                _normalize_keyword_list(faculty.keywords)
                + _normalize_keyword_list(faculty.themes)
                + _normalize_keyword_list(faculty.categories)
            )
            return [k.lower() for k in merged][:25]
    return []


def _match_score(seed, candidate_terms):
    """Overlap of seed terms against a candidate's terms, 0-100."""
    if not seed:
        return 0.0, []
    lowered = {t.lower() for t in candidate_terms if t}
    shared = []
    for term in seed:
        for cand in lowered:
            if term in cand or cand in term:
                shared.append(cand)
                break
    if not shared:
        return 0.0, []
    score = 100.0 * len(shared) / len(seed)
    return round(min(score, 100.0), 2), sorted(set(shared))[:12]


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@api_view(["GET"])
@permission_classes([AllowAny])
def network_discovery(request):
    try:
        query = (request.query_params.get("q") or "").strip()
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 100))

        seed = _seed_keywords(request, query)

        me = None
        if request.user and request.user.is_authenticated:
            me = Faculty.objects.filter(user=request.user).first()

        colleagues = []
        for member in _visible_faculty_qs().exclude(pk=getattr(me, "pk", None)):
            terms = (
                _normalize_keyword_list(member.keywords)
                + _normalize_keyword_list(member.themes)
                + _normalize_keyword_list(member.categories)
                + [member.department or "", member.title or ""]
            )
            score, shared = _match_score(seed, terms)
            if seed and score <= 0:
                continue
            photo_url = ""
            if member.photo:
                try:
                    photo_url = request.build_absolute_uri(member.photo.url)
                except Exception:
                    photo_url = ""
            colleagues.append(
                {
                    "id": str(member.pk),
                    "name": _full_name(
                        member.first_name,
                        member.last_name,
                        (member.name or "").strip() or member.faculty_id,
                    ),
                    "title": member.title or "",
                    "department": member.department or "",
                    "school": member.office or "",
                    "photo": photo_url,
                    "email": member.email or "",
                    "bio": member.bio or "",
                    "keywords": _normalize_keyword_list(member.keywords)[:12],
                    "sharedKeywords": shared,
                    "matchScore": score,
                    "matchReason": (
                        f"Shares {len(shared)} research term(s) with your focus."
                        if shared
                        else "Available SU faculty profile."
                    ),
                    "collaborationScore": member.article_count or 0,
                    "articleCount": member.article_count or 0,
                    "totalCitations": member.total_citations or 0,
                    "directoryVerified": bool(member.directory_verified),
                }
            )

        colleagues.sort(
            key=lambda c: (c["matchScore"], c["totalCitations"]), reverse=True
        )
        colleagues = colleagues[:limit]

        papers = []
        paper_qs = Paper.objects.prefetch_related("authors")
        if query:
            paper_qs = paper_qs.filter(
                Q(title__icontains=query)
                | Q(abstract__icontains=query)
                | Q(keywords__icontains=query)
            )
        for paper in paper_qs[: limit * 3]:
            terms = _normalize_keyword_list(paper.keywords) + _normalize_keyword_list(
                paper.themes
            )
            score, shared = _match_score(seed, terms + [paper.title or ""])
            if seed and score <= 0:
                continue
            papers.append(
                {
                    "id": str(paper.pk),
                    "title": paper.title or "",
                    "authors": [
                        a.name or f"{a.first_name or ''} {a.last_name or ''}".strip()
                        for a in paper.authors.all()
                    ],
                    "journal": paper.journal or "",
                    "year": _year_from_dates(
                        paper.date_published_online,
                        paper.date_published_print,
                        paper.date_published,
                    )
                    or 0,
                    "abstract": paper.abstract or "",
                    "link": paper.url or paper.download_url or "",
                    "keywords": _normalize_keyword_list(paper.keywords)[:12],
                    "departments": [],
                    "schools": [],
                    "citations": paper.tc_count or 0,
                    "relevanceScore": score,
                    "relevanceReason": "Matches your research terms."
                    if shared
                    else "Recent SU publication.",
                    "sharedKeywords": shared,
                }
            )
        papers.sort(key=lambda p: (p["relevanceScore"], p["citations"]), reverse=True)
        papers = papers[:limit]

        patents = []
        for patent in Patent.objects.prefetch_related("faculty")[: limit * 2]:
            score, shared = _match_score(seed, [patent.title or ""])
            patents.append(
                {
                    "id": str(patent.pk),
                    "title": patent.title or "",
                    "inventors": [
                        _full_name(f.first_name, f.last_name, f.name or "")
                        for f in patent.faculty.all()
                    ],
                    "patentNumber": patent.patent_number or "",
                    "year": patent.issue_date.year if patent.issue_date else 0,
                    "description": patent.abstract or "",
                    "link": patent.link or "",
                    "keywords": _normalize_keyword_list(patent.aiKeywords)[:12],
                    "departments": [],
                    "schools": [],
                    "relevanceScore": score,
                    "relevanceReason": "Matches your research terms."
                    if shared
                    else "SU patent record.",
                    "sharedKeywords": shared,
                }
            )
        patents.sort(key=lambda p: p["relevanceScore"], reverse=True)
        patents = patents[:limit]

        projects = []
        for project in Project.objects.prefetch_related("faculty")[: limit * 2]:
            terms = _normalize_keyword_list(project.keywords) + [project.title or ""]
            score, shared = _match_score(seed, terms)
            projects.append(
                {
                    "id": str(project.pk),
                    "title": project.title or "",
                    "leadFaculty": [
                        _full_name(f.first_name, f.last_name, f.name or "")
                        for f in project.faculty.all()
                    ],
                    "department": "",
                    "school": "",
                    "description": project.description or "",
                    "status": project.status or "Active",
                    "keywords": _normalize_keyword_list(project.keywords)[:12],
                    "relevanceScore": score,
                    "relevanceReason": "Matches your research terms."
                    if shared
                    else "Active SU project.",
                    "sharedKeywords": shared,
                }
            )
        projects.sort(key=lambda p: p["relevanceScore"], reverse=True)
        projects = projects[:limit]

        suggested = Counter()
        for colleague in colleagues:
            for term in colleague["keywords"]:
                suggested[term] += 1

        return Response(
            {
                "profileKeywords": seed,
                "expandedTerms": seed,
                "suggestedCategories": [c for c, _ in suggested.most_common(12)],
                "colleagues": colleagues,
                "papers": papers,
                "patents": patents,
                "projects": projects,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as exc:
        logger.exception("network_discovery failed: %s", exc)
        return Response(
            {"error": "service_unavailable", "detail": "Internal server error."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def network_inquire(request):
    data = request.data if isinstance(request.data, dict) else {}

    target_name = str(data.get("target_faculty_name") or "").strip()
    if not target_name:
        return Response(
            {"detail": "target_faculty_name is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = request.user if request.user and request.user.is_authenticated else None

    requester_name = str(data.get("requester_name") or "").strip()
    requester_email = str(data.get("requester_email") or "").strip()

    if user is None:
        # Unauthenticated submissions must identify themselves and are rate limited.
        if not requester_name or not requester_email:
            return Response(
                {"detail": "requester_name and requester_email are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_email(requester_email)
        except ValidationError:
            return Response(
                {"detail": "A valid requester_email is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        window_start = timezone.now() - timezone.timedelta(hours=1)
        recent = NetworkInquiry.objects.filter(
            created_ip=_client_ip(request), created_at__gte=window_start
        ).count()
        if recent >= PUBLIC_INQUIRY_HOURLY_LIMIT:
            return Response(
                {"detail": "Too many requests. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

    target_faculty = None
    target_id = str(data.get("target_faculty_id") or "").strip()
    if target_id:
        target_faculty = Faculty.objects.filter(
            Q(faculty_id=target_id) | Q(pk=target_id if target_id.isdigit() else None)
        ).first()

    inquiry = NetworkInquiry.objects.create(
        target_faculty=target_faculty,
        target_faculty_name=target_name[:255],
        target_department=str(data.get("target_department") or "")[:255],
        target_school=str(data.get("target_school") or "")[:255],
        target_project_id=str(data.get("target_project_id") or "")[:64],
        target_project_title=str(data.get("target_project_title") or "")[:500],
        requester=user,
        requester_name=(requester_name or (user.get_full_name() if user else ""))[:255],
        requester_email=(requester_email or (user.email if user else ""))[:254],
        requester_organization=str(data.get("requester_organization") or "")[:255],
        requester_role=str(data.get("requester_role") or "")[:120],
        shared_keywords=_normalize_keyword_list(data.get("shared_keywords")),
        note=str(data.get("note") or "")[:MAX_NOTE_LENGTH],
        created_ip=_client_ip(request),
    )

    return Response(
        {"id": inquiry.pk, "status": inquiry.status, "detail": "Inquiry submitted."},
        status=status.HTTP_201_CREATED,
    )


def _serialize_inquiry(inquiry):
    return {
        "id": inquiry.pk,
        "target_faculty_name": inquiry.target_faculty_name,
        "target_department": inquiry.target_department,
        "requester_name": inquiry.requester_name,
        "requester_email": inquiry.requester_email,
        "requester_organization": inquiry.requester_organization,
        "requester_role": inquiry.requester_role,
        "shared_keywords": inquiry.shared_keywords or [],
        "note": inquiry.note,
        "status": inquiry.status,
        "created_at": inquiry.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def faculty_inquiries(request):
    faculty = Faculty.objects.filter(user=request.user).first()
    if not faculty:
        return Response([], status=status.HTTP_200_OK)

    inquiries = NetworkInquiry.objects.filter(
        Q(target_faculty=faculty) | Q(target_faculty_name__iexact=faculty.name or "")
    )
    return Response(
        [_serialize_inquiry(i) for i in inquiries], status=status.HTTP_200_OK
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def faculty_inquiry_detail(request, pk):
    faculty = Faculty.objects.filter(user=request.user).first()
    inquiry = NetworkInquiry.objects.filter(pk=pk).first()
    if not inquiry:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    owns = faculty and (
        inquiry.target_faculty_id == faculty.pk
        or (inquiry.target_faculty_name or "").lower() == (faculty.name or "").lower()
    )
    if not owns and not request.user.is_staff:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    new_status = str((request.data or {}).get("status") or "").strip()
    if new_status not in {"new", "reviewed", "closed"}:
        return Response(
            {"detail": "status must be one of: new, reviewed, closed."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    inquiry.status = new_status
    inquiry.save(update_fields=["status", "updated_at"])
    return Response(_serialize_inquiry(inquiry), status=status.HTTP_200_OK)
