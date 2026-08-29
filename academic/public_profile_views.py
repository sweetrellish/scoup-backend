"""Public, read-only faculty profile endpoint.

`FacultyDetailView` (faculty/<pk>/) is IsAuthenticated + self-or-staff only, so
there has never been a page a visitor can land on from search/categories results.
This is deliberately read-only and returns no contact PII beyond what the SU
directory already publishes (room, extension) - introductions go through the
existing throttled /network/inquire/ endpoint, not direct email.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from academic.models import Faculty
from academic.views import _su_affiliated


@api_view(["GET"])
@permission_classes([AllowAny])
def faculty_public_profile(request, pk):
    try:
        member = (
            Faculty.objects.filter(_su_affiliated())
            .filter(pk=pk, profile_visibility=True)
            .exclude(review_status="rejected")
            .get()
        )
    except Faculty.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    papers = (
        member.papers.all()
        .order_by("-tc_count", "-date_published")
        .values("id", "title", "doi", "journal", "date_published", "tc_count", "url")[:50]
    )

    return Response(
        {
            "id": member.pk,
            "name": member.name or f"{member.first_name or ''} {member.last_name or ''}".strip(),
            "title": member.title or "",
            "department": member.department or "",
            "school": member.school or "",
            "room": member.room or "",
            "phone": member.phone or "",
            "photo": member.photo.url if member.photo else None,
            "bio": member.bio or "",
            "orcid": member.orcid or "",
            "directoryVerified": member.directory_verified,
            "articleCount": member.article_count,
            "totalCitations": member.total_citations,
            "averageCitations": member.average_citations,
            "expertise": member.expertise if hasattr(member, "expertise") else None,
            "academic": member.academic if hasattr(member, "academic") else None,
            "practice": member.practice if hasattr(member, "practice") else None,
            "publication": member.publication if hasattr(member, "publication") else None,
            "keywords": (member.keywords or [])[:20],
            "categories": (member.categories or [])[:20],
            "papers": [
                {
                    "id": p["id"],
                    "title": p["title"],
                    "doi": p["doi"],
                    "journal": p["journal"],
                    "year": p["date_published"].year if p["date_published"] else None,
                    "citations": p["tc_count"],
                    "url": p["url"],
                }
                for p in papers
            ],
        }
    )
