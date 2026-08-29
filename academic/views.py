import logging
import re
import uuid
from collections import Counter
from rest_framework.permissions import BasePermission


import pdfplumber
from django.contrib.auth.models import User
from django.db import transaction, DatabaseError
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpResponse
from django.utils.text import slugify
from rest_framework import filters, generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Faculty,
    FacultySuggestionDecision,
    Paper,
    PaperAuthorship,
    Patent,
    Project,
)
from .serializers import (
    FacultyProfileSerializer,
    FacultySerializer,
    PaperSerializer,
    PatentSerializer,
    ProjectSerializer,
)
from .semantic import cosine_similarity, create_query_embedding

logger = logging.getLogger(__name__)

def _normalize_keyword_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _year_from_dates(*dates):
    for dt in dates:
        if dt:
            return dt.year
    return None


def _generate_signup_faculty_id():
    generated_faculty_id = f"SIGNUP-{uuid.uuid4().hex[:12]}"
    while Faculty.objects.filter(faculty_id=generated_faculty_id).exists():
        generated_faculty_id = f"SIGNUP-{uuid.uuid4().hex[:12]}"
    return generated_faculty_id


def _full_name(first_name, last_name, fallback=""):
    return f"{(first_name or '').strip()} {(last_name or '').strip()}".strip() or fallback


def _keywords_for_matching(faculty):
    merged = (
        _normalize_keyword_list(getattr(faculty, "keywords", None))
        + _normalize_keyword_list(getattr(faculty, "faculty_keywords", None))
        + _normalize_keyword_list(getattr(faculty, "ai_keywords", None))
    )
    return {item.lower() for item in merged if item}


def _merge_unique_list(*values):
    ordered = []
    seen = set()
    for value in values:
        items = _normalize_keyword_list(value)
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(item)
    return ordered


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed
        else:
            return value
    return None


def _email_available_for_faculty(email, internal_id, external_id):
    if not email:
        return False
    return not Faculty.objects.filter(email__iexact=email).exclude(
        id__in=[internal_id, external_id]
    ).exists()


def _absorb_external_faculty(internal, external):
    if not external or external.id == internal.id:
        return {"papers": 0, "projects": 0, "patents": 0, "authorships": 0}

    internal.first_name = _first_non_empty(internal.first_name, external.first_name) or ""
    internal.last_name = _first_non_empty(internal.last_name, external.last_name) or ""
    internal.name = _first_non_empty(internal.name, external.name, _full_name(internal.first_name, internal.last_name, ""))
    internal.title = _first_non_empty(internal.title, external.title)
    internal.department = _first_non_empty(internal.department, external.department)
    internal.office = _first_non_empty(internal.office, external.office)
    internal.room = _first_non_empty(internal.room, external.room)
    internal.phone = _first_non_empty(internal.phone, external.phone)
    internal.bio = _first_non_empty(internal.bio, external.bio)

    candidate_email = _first_non_empty(internal.email, external.email)
    if _email_available_for_faculty(candidate_email, internal.id, external.id):
        assert candidate_email is not None
        internal.email = candidate_email.lower()

    internal.department_affiliations = _merge_unique_list(
        internal.department_affiliations, external.department_affiliations
    )
    internal.dois = _merge_unique_list(internal.dois, external.dois)
    internal.titles = _merge_unique_list(internal.titles, external.titles)
    internal.categories = _merge_unique_list(internal.categories, external.categories)
    internal.top_level_categories = _merge_unique_list(
        internal.top_level_categories, external.top_level_categories
    )
    internal.mid_level_categories = _merge_unique_list(
        internal.mid_level_categories, external.mid_level_categories
    )
    internal.low_level_categories = _merge_unique_list(
        internal.low_level_categories, external.low_level_categories
    )
    internal.category_urls = _merge_unique_list(internal.category_urls, external.category_urls)
    internal.top_category_urls = _merge_unique_list(
        internal.top_category_urls, external.top_category_urls
    )
    internal.mid_category_urls = _merge_unique_list(
        internal.mid_category_urls, external.mid_category_urls
    )
    internal.low_category_urls = _merge_unique_list(
        internal.low_category_urls, external.low_category_urls
    )
    internal.themes = _merge_unique_list(internal.themes, external.themes)
    internal.journals = _merge_unique_list(internal.journals, external.journals)
    internal.keywords = _merge_unique_list(internal.keywords, external.keywords)
    internal.faculty_keywords = ", ".join(
        _merge_unique_list(internal.faculty_keywords, external.faculty_keywords)
    )
    internal.ai_keywords = ", ".join(
        _merge_unique_list(internal.ai_keywords, external.ai_keywords)
    )

    internal.total_citations = max(internal.total_citations or 0, external.total_citations or 0)
    internal.article_count = max(internal.article_count or 0, external.article_count or 0)
    internal.average_citations = max(
        internal.average_citations or 0.0, external.average_citations or 0.0
    )
    internal.save()

    papers_added = 0
    for paper in external.papers.all():
        before = paper.authors.filter(id=internal.id).exists()
        paper.authors.add(internal)
        if not before:
            papers_added += 1

    projects_added = 0
    for project in external.projects.all():
        before = project.faculty.filter(id=internal.id).exists()
        project.faculty.add(internal)
        if not before:
            projects_added += 1

    patents_added = 0
    for patent in external.patents.all():
        before = patent.faculty.filter(id=internal.id).exists()
        patent.faculty.add(internal)
        if not before:
            patents_added += 1

    authorships_added = 0
    for authorship in external.authorships.all():
        _, created = PaperAuthorship.objects.get_or_create(
            paper=authorship.paper,
            faculty=internal,
            defaults={"status": authorship.status, "decided_at": authorship.decided_at},
        )
        if created:
            authorships_added += 1

    external.profile_visibility = False
    external.is_approved = False
    external.save(update_fields=["profile_visibility", "is_approved"])

    return {
        "papers": papers_added,
        "projects": projects_added,
        "patents": patents_added,
        "authorships": authorships_added,
    }


def _external_faculty_preview_payload(external):
    papers = [
        {
            "id": paper.id,
            "doi": paper.doi,
            "title": paper.title,
            "year": _year_from_dates(
                paper.date_published_online, paper.date_published_print, paper.date_published
            ),
            "journal": paper.journal or "",
        }
        for paper in external.papers.all()[:50]
    ]
    projects = [
        {
            "id": project.id,
            "title": project.title,
            "status": project.status or "",
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
        }
        for project in external.projects.all()[:50]
    ]
    patents = [
        {
            "id": patent.id,
            "title": patent.title,
            "patent_number": patent.patent_number or "",
            "issue_year": patent.issue_date.year if patent.issue_date else None,
        }
        for patent in external.patents.all()[:50]
    ]

    return {
        "faculty": {
            "id": external.id,
            "faculty_id": external.faculty_id,
            "name": _full_name(
                external.first_name,
                external.last_name,
                (external.name or "").strip() or external.faculty_id,
            ),
            "department": external.department or "",
            "title": external.title or "",
            "email": external.email or "",
            "keywords": _normalize_keyword_list(external.keywords)
            or _normalize_keyword_list(external.faculty_keywords)
            or _normalize_keyword_list(external.ai_keywords),
        },
        "papers": papers,
        "projects": projects,
        "patents": patents,
        "counts": {
            "papers": external.papers.count(),
            "projects": external.projects.count(),
            "patents": external.patents.count(),
        },
    }


def _get_request_faculty(user, create_if_missing=False):
    faculty = Faculty.objects.filter(user=user).first()
    if faculty:
        if faculty.faculty_id and not faculty.is_approved:
            faculty.is_approved = True
            faculty.save(update_fields=["is_approved", "updated_at"])
        return faculty

    email = (user.email or "").strip()
    if not create_if_missing:
        return None

    email_in_use_elsewhere = (
        bool(email)
        and Faculty.objects.filter(email__iexact=email).exclude(user=user).exists()
    )
    safe_email = None if email_in_use_elsewhere else (email.lower() if email else None)

    return Faculty.objects.create(
        user=user,
        faculty_id=_generate_signup_faculty_id(),
        email=safe_email,
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        name=_full_name(user.first_name, user.last_name, user.username),
        is_approved=True,
        profile_visibility=True,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def public_search_data(request):
    try:
        faculty_qs = (
            _visible_faculty_qs()
            .prefetch_related("projects", "patents")
            .select_related("user")
            .order_by("last_name", "first_name")
        )
        papers_qs = (
            Paper.objects.defer("paper_embedding", "embedding_model", "embedding_updated_at")
            .prefetch_related("authors")
            .order_by("-id")
        )
        projects_qs = Project.objects.all().prefetch_related("faculty").order_by("-id")
        patents_qs = Patent.objects.all().prefetch_related("faculty").order_by("-id")

        faculty = []
        for item in faculty_qs:
            user_first_name = (item.user.first_name if item.user else "") or ""
            user_last_name = (item.user.last_name if item.user else "") or ""
            user_username = (item.user.username if item.user else "") or ""

            full_name = _full_name(
                item.first_name or user_first_name,
                item.last_name or user_last_name,
                (item.name or "").strip() or user_username or item.email or item.faculty_id,
            )
            merged_keywords = _merge_unique_list(
               item.keywords, item.faculty_keywords, item.ai_keywords
            )
            merged_keywords = [k for k in (merged_keywords or []) if k]

            # FileField.url can raise if file missing or storage misconfigured
            photo_url = ""
            if item.photo:
                try:
                    photo_url = request.build_absolute_uri(item.photo.url)
                except Exception:
                    photo_url = ""

            faculty.append(
                {
                    "id": str(item.faculty_id),
                    "name": full_name,
                    "title": item.title or "",
                    "department": item.department or "",
                    "email": item.email or "",
                    "phone": item.phone or "",
                    "photo": photo_url,
                    "bio": item.bio or "",
                    "researchInterests": merged_keywords[:8],
                    "aiKeywords": merged_keywords,
                    "metricsProfile": {
                        "totalCitations": item.total_citations or 0,
                        "articleCount": item.article_count or 0,
                        "averageCitations": item.average_citations or 0.0,
                    },
                    "categories": {
                        "top": _normalize_keyword_list(item.top_level_categories),
                        "mid": _normalize_keyword_list(item.mid_level_categories),
                        "low": _normalize_keyword_list(item.low_level_categories),
                    },
                    "themes": _normalize_keyword_list(item.themes),
                    "journals": _normalize_keyword_list(item.journals),
                }
            )

        papers = []
        for item in papers_qs:
            year = _year_from_dates(
                item.date_published_online, item.date_published_print, item.date_published
            )
            papers.append(
                {
                    "id": str(item.pk),
                    "title": item.title or "",
                    "doi": item.doi or "",
                    "journal": item.journal or "",
                    "authors": [
                        author.name
                        or f"{author.first_name or ''} {author.last_name or ''}".strip()
                        for author in item.authors.all()
                    ],
                    "year": year or 0,
                    "abstract": item.abstract or "",
                    # keep the rest of your paper fields here...
                }
            )

        projects = []
        for item in projects_qs:
            projects.append(
                {
                    "id": str(item.pk),
                    "title": getattr(item, "title", "") or "",
                    "description": getattr(item, "description", "") or "",
                }
            )

        patents = []
        for item in patents_qs:
            patents.append(
                {
                    "id": str(item.pk),
                    "title": getattr(item, "title", "") or "",
                    "number": getattr(item, "patent_number", "") or getattr(item, "number", "") or "",
                }
            )
#new logic added to help debug aikeywords not being populated -RE 4/10/2026 ERROR:Failed to load backend dataset: TypeError: can't access property "forEach", n.aiKeywords is undefined
        for f in faculty:
            if not isinstance(f.get("aiKeywords"), list):
                f["aiKeywords"] = []
            if not isinstance(f.get("researchInterests"), list):
                f["researchInterests"] = []
        for p in papers:
            if not isinstance(p.get("aiKeywords"), list):
                p["aiKeywords"] = []
        for pat in patents:
            if not isinstance(pat.get("aiKeywords"), list):
                pat["aiKeywords"] = []
        for proj in projects:
            if not isinstance(proj.get("aiKeywords"), list):
                proj["aiKeywords"] = []
        for proj in projects:
            if "leadFaculty" not in proj or not isinstance(proj["leadFaculty"], list):
                proj["leadFaculty"] = []
        for pat in patents:
            if "inventors" not in pat or not isinstance(pat["inventors"], list):
                pat["inventors"] = []
        return Response(
            {
                "facultyData": faculty,
                "papersData": papers,
                "patentsData": patents,
                "projectsData": projects,
            },
            status=status.HTTP_200_OK,
        )
    except Exception  as e:
        # Log full detail server-side
        logger.exception("public_search_data failed (unexpected): %s", e)
        return Response(
            {
                "error": "service_unavailable",
                "detail": "Internal server error."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
_SEARCH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "to", "with",
}

# Titles/abstracts are author-written and trustworthy. The `keywords` taxonomy is
# machine-assigned upstream and noisy, so it is deliberately weighted lower.
_FIELD_WEIGHTS = {
    "title": 5.0,
    "themes": 3.0,
    "abstract": 2.5,
    "keywords": 2.0,
    "journal": 1.0,
}

# Umbrella tags the upstream classifier applies broadly; matching only these is weak evidence.
_GENERIC_TAG_MARKERS = (
    " nec",
    ", other",
    ", general",
    "multidisciplinary interdisciplinary",
    "interdisciplinary computer sciences",
)

_MIN_CONFIDENCE = 30.0


def _tokenize_query(text):
    tokens = [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]
    meaningful = [t for t in tokens if t not in _SEARCH_STOPWORDS and len(t) > 1]
    return meaningful or tokens


def _word_match(token, text):
    if not text:
        return False
    # Missing trailing \b let "AI" match inside "Air" (e.g. "Open-Air Factor").
    return re.search(r"\b" + re.escape(token) + r"\b", text) is not None


def _is_generic_tag(tag):
    lowered = tag.lower()
    return any(marker in lowered for marker in _GENERIC_TAG_MARKERS)


# Abstracts in this dataset embed author/affiliation boilerplate (e.g. "Department of
# Math and Computer Science, Salisbury University"), which produced false keyword matches.
_AFFILIATION_LINE = re.compile(
    r"\b(department|dept\.?|school|college|institute|laborator(?:y|ies)|centre|center)\b"
    r".*\b(universit|college|institute)",
    re.IGNORECASE,
)
_BOILERPLATE_HEADER = re.compile(
    r"^\s*\**\s*(affiliations?|authors?|doi|cited by|notes on contributors|"
    r"additional information|acknowledge?ments?)\s*:?\s*\**\s*$",
    re.IGNORECASE,
)


def _clean_abstract(text):
    if not text:
        return ""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _BOILERPLATE_HEADER.match(stripped):
            continue
        if _AFFILIATION_LINE.search(stripped):
            continue
        kept.append(stripped)
    return " ".join(kept)


def _paper_field_text(paper):
    return {
        "title": (paper.title or "").lower(),
        "keywords": " ".join(_normalize_keyword_list(paper.keywords)).lower(),
        "themes": " ".join(_normalize_keyword_list(paper.themes)).lower(),
        "abstract": _clean_abstract(paper.abstract).lower(),
        "journal": (paper.journal or "").lower(),
    }


def _score_paper(paper, tokens, phrase):
    """Return (confidence 0-100, matched field names) for a lexical match."""
    fields = _paper_field_text(paper)
    keyword_list = _normalize_keyword_list(paper.keywords)

    matched_fields = set()
    matched_tokens = 0
    weight_sum = 0.0

    for token in tokens:
        token_weight = 0.0
        for field_name, text in fields.items():
            if _word_match(token, text):
                token_weight = max(token_weight, _FIELD_WEIGHTS[field_name])
                matched_fields.add(field_name)
        if token_weight:
            matched_tokens += 1
            weight_sum += token_weight

    if not matched_tokens:
        return 0.0, []

    coverage = matched_tokens / len(tokens)

    # Every term must appear somewhere; partial matches are what produced unrelated
    # results (e.g. "computer science" matching papers on "science" alone).
    if len(tokens) > 1 and coverage < 1.0:
        return 0.0, []

    # depth: how strong the best field was for each matched term
    density = weight_sum / (matched_tokens * _FIELD_WEIGHTS["title"])
    # breadth: how many independent fields corroborate the match
    breadth = sum(_FIELD_WEIGHTS[f] for f in matched_fields) / sum(_FIELD_WEIGHTS.values())

    confidence = 100.0 * (0.45 * density + 0.25 * breadth + 0.30 * coverage)

    exact_keyword = any(k.lower() == phrase for k in keyword_list)
    if exact_keyword:
        confidence += 12.0
    if phrase and len(tokens) > 1:
        if phrase in fields["title"]:
            confidence += 12.0
        elif phrase in fields["themes"]:
            confidence += 6.0
        elif phrase in fields["abstract"]:
            confidence += 3.0

    # Matching only broad auto-assigned tags is weak evidence of true relevance.
    if matched_fields == {"keywords"} and not exact_keyword:
        hits = [k for k in keyword_list if any(_word_match(t, k.lower()) for t in tokens)]
        if hits and all(_is_generic_tag(k) for k in hits):
            confidence *= 0.5

    return min(round(confidence, 2), 100.0), sorted(matched_fields)


class _SearchFilters:
    """Optional post-ranking filters for /api/search/.

    Relevance scoring is unchanged - these only remove results that do not meet
    an explicit constraint, so a filtered search returns a subset of the same
    ranking rather than a differently ranked list.
    """

    __slots__ = ("year_min", "year_max", "journal", "min_citations", "has_abstract", "sort")

    def __init__(self, params):
        self.year_min = _as_int(params.get("year_min"))
        self.year_max = _as_int(params.get("year_max"))
        self.journal = (params.get("journal") or "").strip().lower()
        self.min_citations = _as_int(params.get("min_citations"))
        self.has_abstract = (params.get("has_abstract") or "").lower() == "true"
        sort = (params.get("sort") or "relevance").strip().lower()
        self.sort = sort if sort in {"relevance", "citations", "year"} else "relevance"

    @property
    def active(self):
        return any(
            (
                self.year_min is not None,
                self.year_max is not None,
                self.journal,
                self.min_citations is not None,
                self.has_abstract,
            )
        )

    def keep(self, year, journal, citations, abstract):
        if self.year_min is not None and (year or 0) < self.year_min:
            return False
        if self.year_max is not None and (year or 0) > self.year_max:
            return False
        if self.journal and self.journal not in (journal or "").lower():
            return False
        if self.min_citations is not None and (citations or 0) < self.min_citations:
            return False
        if self.has_abstract and not (abstract or "").strip():
            return False
        return True

    def describe(self):
        applied = {}
        if self.year_min is not None:
            applied["year_min"] = self.year_min
        if self.year_max is not None:
            applied["year_max"] = self.year_max
        if self.journal:
            applied["journal"] = self.journal
        if self.min_citations is not None:
            applied["min_citations"] = self.min_citations
        if self.has_abstract:
            applied["has_abstract"] = True
        applied["sort"] = self.sort
        return applied


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_sort(results, sort):
    """Re-order finished result dicts. Relevance order is left untouched."""
    if sort == "citations":
        return sorted(results, key=lambda r: (r.get("citations") or 0), reverse=True)
    if sort == "year":
        return sorted(results, key=lambda r: (r.get("year") or 0), reverse=True)
    return results


def _lexical_paper_search(query, limit, filters=None):
    """Weighted keyword ranking used when embeddings are unavailable."""
    tokens = _tokenize_query(query)
    if not tokens:
        return []

    phrase = " ".join(query.lower().split())

    candidate_filter = Q()
    for token in tokens:
        candidate_filter |= (
            Q(title__icontains=token)
            | Q(abstract__icontains=token)
            | Q(journal__icontains=token)
            | Q(keywords__icontains=token)
            | Q(themes__icontains=token)
        )

    candidates = Paper.objects.filter(candidate_filter).prefetch_related("authors")

    scored = []
    for paper in candidates:
        confidence, matched_on = _score_paper(paper, tokens, phrase)
        if confidence < _MIN_CONFIDENCE:
            continue
        year = _year_from_dates(
            paper.date_published_online, paper.date_published_print, paper.date_published
        )
        if filters and not filters.keep(year, paper.journal, paper.tc_count, paper.abstract):
            continue
        scored.append((confidence, paper.tc_count or 0, year or 0, paper, matched_on))

    # Citations and recency break ties only, so they cannot outrank relevance.
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

    results = []
    for confidence, citations, year, paper, matched_on in scored[:limit]:
        results.append(
            {
                "id": str(paper.pk),
                "title": paper.title or "",
                "doi": paper.doi or "",
                "journal": paper.journal or "",
                "authors": [
                    author.name
                    or f"{author.first_name or ''} {author.last_name or ''}".strip()
                    for author in paper.authors.all()
                ],
                "year": year,
                "abstract": paper.abstract or "",
                "link": paper.url or paper.download_url or paper.license_url or "",
                "citations": citations,
                "aiKeywords": _normalize_keyword_list(paper.keywords),
                "matchedOn": matched_on,
                "semanticScore": confidence,
                "confidence": confidence,
            }
        )
    return _apply_sort(results, filters.sort) if filters else results


@api_view(["GET"])
@permission_classes([AllowAny])
def semantic_paper_search(request):
    query = (request.query_params.get("q") or "").strip()
    if len(query) < 2:
        return Response({"results": [], "count": 0, "detail": "Query too short."})

    try:
        limit = int(request.query_params.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))

    model = (request.query_params.get("model") or "text-embedding-3-small").strip()
    filters = _SearchFilters(request.query_params)

    try:
        query_embedding = create_query_embedding(query, model=model)
    except Exception as exc:
        results = _lexical_paper_search(query, limit, filters)
        return Response(
            {
                "query": query,
                "model": model,
                "count": len(results),
                "results": results,
                "filters": filters.describe(),
                "detail": f"Embeddings unavailable; used ranked keyword search. ({exc})",
            },
            status=status.HTTP_200_OK,
        )
    try:
        papers = (
            Paper.objects.exclude(paper_embedding=[])
            .exclude(paper_embedding__isnull=True)
            .prefetch_related("authors")
        )
    except OperationalError as exc:
        return Response(
            {
                "results": [],
                "count": 0,
                "detail": f"Semantic search schema unavailable: {exc}. Run migrations.",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    scored = []
    for paper in papers:
        embedding = paper.paper_embedding or []
        score = cosine_similarity(query_embedding, embedding)
        if score <= 0:
            continue
        # Filtered before the top-N cut, so a filtered search still fills `limit`.
        if not filters.keep(
            _year_from_dates(
                paper.date_published_online, paper.date_published_print, paper.date_published
            ),
            paper.journal,
            paper.tc_count,
            paper.abstract,
        ):
            continue
        score_100 = round(score * 100.0, 2)
        scored.append((score_100, paper))

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:limit]

    results = []
    for semantic_score, paper in top:
        year = _year_from_dates(
            paper.date_published_online, paper.date_published_print, paper.date_published
        )
        results.append(
            {
                "id": str(paper.id),
                "title": paper.title or "",
                "doi": paper.doi or "",
                "journal": paper.journal or "",
                "authors": [
                    author.name
                    or f"{author.first_name or ''} {author.last_name or ''}".strip()
                    for author in paper.authors.all()
                ],
                "year": year or 0,
                "abstract": paper.abstract or "",
                "link": paper.url or paper.download_url or paper.license_url or "",
                "citations": paper.tc_count or 0,
                "publishedOnline": paper.date_published_online.isoformat()
                if paper.date_published_online
                else "",
                "publishedPrint": paper.date_published_print.isoformat()
                if paper.date_published_print
                else "",
                "aiKeywords": _normalize_keyword_list(paper.keywords)
                or _normalize_keyword_list(paper.ai_keywords)
                or _normalize_keyword_list(paper.faculty_keywords),
                "categories": {
                    "top": _normalize_keyword_list(paper.top_level_categories),
                    "mid": _normalize_keyword_list(paper.mid_level_categories),
                    "low": _normalize_keyword_list(paper.low_level_categories),
                },
                "facultyMembers": _normalize_keyword_list(paper.faculty_members),
                "facultyAffiliations": paper.faculty_affiliations or {},
                "sourceMetadata": paper.source_metadata or {},
                "engagementMetrics": paper.engagement_metrics or {},
                "semanticScore": semantic_score,
            }
        )

    results = _apply_sort(results, filters.sort)

    return Response(
        {
            "query": query,
            "model": model,
            "filters": filters.describe(),
            "count": len(results),
            "results": results,
        }
    )



# OpenAlex concepts are extremely fine-grained - most groups have a single paper
# (e.g. "APACHE II"). Browsing needs a floor, scaled to corpus size so it does not
# drift as more works are ingested. Callers can override with ?min_count=.
CATEGORY_CORPUS_DIVISOR = 300
CATEGORY_MIN_FLOOR = 5


def _default_category_min(total_papers):
    return max(CATEGORY_MIN_FLOOR, total_papers // CATEGORY_CORPUS_DIVISOR)


def _split_top_level(name):
    """Derive a top-level grouping from the flat taxonomy string.

    The dataset has no populated hierarchy (top/mid/low_level_categories are empty),
    so the segment before the first comma is used as the grouping key.
    """
    return name.split(",")[0].strip() or name.strip()


# The Faculty table also holds external co-authors imported from publication data
# (1,537 of 1,721 rows). They are needed for paper attribution but are not SU
# faculty, so public-facing views and metrics must exclude them.
def _su_affiliated():
    return (
        Q(directory_verified=True)
        | Q(user__isnull=False)
        | (Q(department__isnull=False) & ~Q(department=""))
    )


def _visible_faculty_qs():
    return (
        Faculty.objects.filter(profile_visibility=True)
        .filter(Q(is_approved=True) | Q(user__isnull=False))
        .filter(_su_affiliated())
    )


def _category_index():
    """Map category name -> {papers: [Paper], faculty: [Faculty]}."""
    index = {}

    for paper in Paper.objects.prefetch_related("authors"):
        for name in _normalize_keyword_list(paper.keywords):
            index.setdefault(name, {"papers": [], "faculty": []})["papers"].append(paper)

    for member in _visible_faculty_qs():
        for name in _normalize_keyword_list(member.categories):
            index.setdefault(name, {"papers": [], "faculty": []})["faculty"].append(member)

    return index


@api_view(["GET"])
@permission_classes([AllowAny])
def query_expansions(request):
    """Abbreviation -> expansion map used by the frontend to widen short queries."""
    return Response(
        {
            "ai": "artificial intelligence",
            "ml": "machine learning",
            "llm": "large language models",
            "nlp": "natural language processing",
            "cs": "computer science",
            "cv": "computer vision",
            "hci": "human computer interaction",
            "iot": "internet of things",
            "gis": "geographic information systems",
            "ph": "public health",
            "psych": "psychology",
            "bio": "biology",
            "chem": "chemistry",
            "econ": "economics",
            "stats": "statistics",
            "eng": "engineering",
            "env": "environmental science",
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def categories_list(request):
    try:
        index = _category_index()

        groups = {}
        for name, bucket in index.items():
            top = _split_top_level(name)
            group = groups.setdefault(
                top, {"papers": set(), "faculty": set(), "children": {}}
            )
            group["papers"].update(p.pk for p in bucket["papers"])
            group["faculty"].update(f.pk for f in bucket["faculty"])
            if name != top:
                group["children"][name] = (
                    len(bucket["papers"]),
                    len(bucket["faculty"]),
                )

        payload = []
        for top, group in groups.items():
            mids = [
                {
                    "name": child,
                    "slug": slugify(child),
                    "article_count": counts[0],
                    "faculty_count": counts[1],
                }
                for child, counts in sorted(group["children"].items())
            ]
            payload.append(
                {
                    "name": top,
                    "slug": slugify(top),
                    "article_count": len(group["papers"]),
                    "faculty_count": len(group["faculty"]),
                    "mid_level_categories": mids,
                }
            )

        fallback_min = _default_category_min(Paper.objects.count())
        try:
            min_articles = max(0, int(request.query_params.get("min_count", fallback_min)))
        except (TypeError, ValueError):
            min_articles = fallback_min
        if min_articles:
            payload = [c for c in payload if c["article_count"] >= min_articles]

        payload.sort(key=lambda item: (-item["article_count"], item["name"]))

        limit_param = request.query_params.get("limit")
        if limit_param:
            try:
                payload = payload[: max(1, int(limit_param))]
            except (TypeError, ValueError):
                pass

        return Response(payload, status=status.HTTP_200_OK)
    except Exception as exc:
        logger.exception("categories_list failed: %s", exc)
        return Response(
            {"error": "service_unavailable", "detail": "Internal server error."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def category_detail(request, category):
    try:
        target = (category or "").strip()
        target_slug = slugify(target)
        index = _category_index()

        # A slug may address either an exact category or a derived top-level group.
        member_names = [
            name
            for name in index
            if name.lower() == target.lower() or slugify(name) == target_slug
        ]
        display_name = member_names[0] if member_names else None

        if not member_names:
            member_names = [
                name for name in index if slugify(_split_top_level(name)) == target_slug
            ]
            if member_names:
                display_name = _split_top_level(member_names[0])

        if not member_names:
            return Response(
                {"detail": "Unknown category.", "category_name": target, "slug": target_slug},
                status=status.HTTP_404_NOT_FOUND,
            )

        papers = {}
        faculty = {}
        for name in member_names:
            for paper in index[name]["papers"]:
                papers[paper.pk] = paper
            for member in index[name]["faculty"]:
                faculty[member.pk] = member

        theme_counts = Counter()
        for paper in papers.values():
            for theme in _normalize_keyword_list(paper.themes):
                theme_counts[theme] += 1

        paper_payload = []
        for paper in papers.values():
            published = (
                paper.date_published_online
                or paper.date_published_print
                or paper.date_published
            )
            paper_payload.append(
                {
                    "id": paper.pk,
                    "title": paper.title or "",
                    "doi": paper.doi or "",
                    "journal": paper.journal or None,
                    "date_published": published.isoformat() if published else None,
                    "tc_count": paper.tc_count or 0,
                    "themes": _normalize_keyword_list(paper.themes),
                    "mid_level_categories": _normalize_keyword_list(paper.keywords),
                    "download_url": paper.download_url or paper.url or None,
                    "authors": [
                        {
                            "id": author.pk,
                            "name": author.name
                            or f"{author.first_name or ''} {author.last_name or ''}".strip(),
                        }
                        for author in paper.authors.all()
                    ],
                }
            )
        paper_payload.sort(key=lambda item: (item["date_published"] or "", item["tc_count"]), reverse=True)

        faculty_payload = []
        departments = set()
        for member in faculty.values():
            if member.department:
                departments.add(member.department)
            photo_url = ""
            if member.photo:
                try:
                    photo_url = request.build_absolute_uri(member.photo.url)
                except Exception:
                    photo_url = ""
            faculty_payload.append(
                {
                    "id": member.pk,
                    "name": _full_name(
                        member.first_name,
                        member.last_name,
                        (member.name or "").strip() or member.faculty_id,
                    ),
                    "department": member.department or None,
                    "title": member.title or None,
                    "total_citations": member.total_citations or 0,
                    "article_count": member.article_count or 0,
                    "photo": photo_url or None,
                    "themes": _normalize_keyword_list(member.themes),
                    "paper_ids": [
                        p.pk for p in papers.values() if member.pk in {a.pk for a in p.authors.all()}
                    ],
                    "is_approved": bool(member.is_approved),
                    "profile_visibility": bool(member.profile_visibility),
                    "email": member.email or "",
                }
            )
        faculty_payload.sort(key=lambda item: item["name"])

        total_citations = sum(item["tc_count"] for item in paper_payload)
        article_count = len(paper_payload)

        return Response(
            {
                "category_name": display_name or target,
                "slug": slugify(display_name or target),
                "stats": {
                    "article_count": article_count,
                    "faculty_count": len(faculty_payload),
                    "department_count": len(departments),
                    "total_citations": total_citations,
                    "citation_average": round(total_citations / article_count, 2)
                    if article_count
                    else 0.0,
                },
                "themes": [
                    {"name": name, "count": count}
                    for name, count in theme_counts.most_common(50)
                ],
                "papers": paper_payload,
                "faculty": faculty_payload,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as exc:
        logger.exception("category_detail failed: %s", exc)
        return Response(
            {"error": "service_unavailable", "detail": "Internal server error."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def home(request):
    return HttpResponse(
        "<h1>Welcome to the Scoup Database!</h1><p>Go to <a href='/admin/'>Admin</a></p>"
    )

class FacultyListCreateView(generics.ListCreateAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "first_name",
        "last_name",
        "name",
        "title",
        "department",
        "faculty_keywords",
        "ai_keywords",
        "keywords",
    ]

    def get_queryset(self):
        return Faculty.objects.filter(profile_visibility=True).filter(
            Q(is_approved=True) | Q(user__isnull=False)
        )

    serializer_class = FacultySerializer

class PaperListCreateView(generics.ListCreateAPIView):
    serializer_class = PaperSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "title",
        "abstract",
        "journal",
        "keywords",
        "themes",
        "authors__name",
        "authors__department",
    ]
    def get_queryset(self):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            return Paper.objects.none()
        return Paper.objects.filter(authors=faculty)

    def perform_create(self, serializer):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            raise NotFound("Faculty profile not found for this user.")
        paper = serializer.save()
        paper.authors.add(faculty)
        paper.save()

class ProjectListCreateView(generics.ListCreateAPIView):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            return Project.objects.none()
        return Project.objects.filter(faculty=faculty)

    def perform_create(self, serializer):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            raise NotFound("Faculty profile not found for this user.")
        project = serializer.save()
        project.faculty.add(faculty)
        project.save()

class PatentListCreateView(generics.ListCreateAPIView):
    serializer_class = PatentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            return Patent.objects.none()
        return Patent.objects.filter(faculty=faculty)

    def perform_create(self, serializer):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            raise NotFound("Faculty profile not found for this user.")
        patent = serializer.save()
        patent.faculty.add(faculty)
        patent.save()

class IsApprovedUpdateOnly(BasePermission):
    """Allow unauthenticated PUT/PATCH to is_approved field only"""
    def has_permission(self, request, view):
        if request.method in ['PUT', 'PATCH']:
            data = request.data or {}
            # Only allow if ONLY is_approved is being updated
            if len(data) == 1 and 'is_approved' in data:
                return True
        # Otherwise require authentication
        return bool(request.user and request.user.is_authenticated)

class FacultyDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = FacultyProfileSerializer
    permission_classes = [IsApprovedUpdateOnly]
    authentication_classes = []
    queryset = Faculty.objects.all()

    def get_object(self):
        obj = super().get_object()
    
    # Allow unauthenticated is_approved updates
        if self.request.method in ['PUT', 'PATCH']:
            data = self.request.data or {}
            if len(data) == 1 and 'is_approved' in data:
                return obj  # Skip permission check for is_approved-only updates
    
        requestor = _get_request_faculty(self.request.user)
        if self.request.user.is_staff or (requestor and obj.id == requestor.id):
            return obj
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("You can only edit your own faculty profile.")


class PaperDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PaperSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            return Paper.objects.none()
        return Paper.objects.filter(authors=faculty)


class ProjectDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            return Project.objects.none()
        return Project.objects.filter(faculty=faculty)


class PatentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PatentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        faculty = _get_request_faculty(self.request.user)
        if not faculty:
            return Patent.objects.none()
        return Patent.objects.filter(faculty=faculty)


@api_view(["POST"])
@permission_classes([AllowAny])
def faculty_signup(request):
    data = request.data
    username = (data.get("username") or "").strip()
    password = data.get("password")
    email = (data.get("email") or "").strip().lower()
    first_name = data.get("first_name")
    last_name = data.get("last_name")

    if not username or not password or not email:
        return Response(
            {"error": "Username, password, and email are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if User.objects.filter(username=username).exists():
        return Response(
            {"error": "Username already exists."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if User.objects.filter(email__iexact=email).exists():
        return Response(
            {"error": "Email already exists."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            first_name=first_name or "",
            last_name=last_name or "",
        )
        email_in_use_elsewhere = Faculty.objects.filter(email__iexact=email).exists()
        safe_email = None if email_in_use_elsewhere else email
        requested_faculty_id = (data.get("faculty_id") or "").strip()
        generated_faculty_id = requested_faculty_id or _generate_signup_faculty_id()
        if Faculty.objects.filter(faculty_id=generated_faculty_id).exists():
            generated_faculty_id = _generate_signup_faculty_id()

        Faculty.objects.create(
            user=user,
            faculty_id=generated_faculty_id,
            email=safe_email,
            first_name=first_name or "",
            last_name=last_name or "",
            name=_full_name(first_name, last_name, username),
            is_approved=True,
            profile_visibility=True,
        )

    return Response(
        {"message": "Faculty account created."},
        status=status.HTTP_201_CREATED,
    )

@api_view(["GET", "PATCH", "PUT"])
@permission_classes([IsAuthenticated])
def faculty_me(request):
    faculty = _get_request_faculty(request.user, create_if_missing=True)
    if not faculty:
        return Response(
            {"detail": "Faculty profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        serializer = FacultyProfileSerializer(faculty)
        return Response(serializer.data)

    partial = request.method == "PATCH"
    serializer = FacultyProfileSerializer(faculty, data=request.data, partial=partial)

    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def faculty_me_suggestions(request):
    faculty = _get_request_faculty(request.user, create_if_missing=True)
    if not faculty:
        return Response(
            {"detail": "Faculty profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND,
        )

    first_name = (faculty.first_name or request.user.first_name or "").strip().lower()
    last_name = (faculty.last_name or request.user.last_name or "").strip().lower()
    full_name = _full_name(faculty.first_name, faculty.last_name).strip().lower()
    department = (faculty.department or "").strip().lower()
    email = (request.user.email or faculty.email or "").strip().lower()
    internal_keywords = _keywords_for_matching(faculty)

    query = Q()
    if first_name:
        query |= Q(first_name__iexact=first_name) | Q(name__icontains=first_name)
    if last_name:
        query |= Q(last_name__iexact=last_name) | Q(name__icontains=last_name)
    if department:
        query |= Q(department__icontains=department)
    if email:
        query |= Q(email__iexact=email)

    rejected_ids = list(
        FacultySuggestionDecision.objects.filter(reviewer=faculty, decision="rejected")
        .values_list("external_faculty_id", flat=True)
    )
    candidates_qs = Faculty.objects.filter(user__isnull=True, profile_visibility=True).exclude(
        id__in=rejected_ids
    )
    if query:
        candidates_qs = candidates_qs.filter(query)
    candidates = candidates_qs.order_by("id")[:200]

    suggestions = []
    for candidate in candidates:
        candidate_first = (candidate.first_name or "").strip().lower()
        candidate_last = (candidate.last_name or "").strip().lower()
        candidate_name = _full_name(
            candidate.first_name,
            candidate.last_name,
            (candidate.name or "").strip(),
        ).strip().lower()
        candidate_department = (candidate.department or "").strip().lower()
        candidate_email = (candidate.email or "").strip().lower()
        candidate_keywords = _keywords_for_matching(candidate)

        score = 0
        reasons = []

        if email and candidate_email and candidate_email == email:
            score += 8
            reasons.append("matching email")
        if full_name and candidate_name and candidate_name == full_name:
            score += 6
            reasons.append("matching full name")
        if last_name and candidate_last and candidate_last == last_name:
            score += 3
            reasons.append("matching last name")
        if first_name and candidate_first and candidate_first == first_name:
            score += 2
            reasons.append("matching first name")
        if department and candidate_department:
            if department == candidate_department:
                score += 2
                reasons.append("matching department")
            elif department in candidate_department or candidate_department in department:
                score += 1
                reasons.append("similar department")

        shared_keywords = sorted(internal_keywords.intersection(candidate_keywords))
        if shared_keywords:
            keyword_points = min(4, len(shared_keywords))
            score += keyword_points
            reasons.append(f"{len(shared_keywords)} shared keywords")

        if score >= 3:
            suggestions.append(
                {
                    "id": candidate.pk,
                    "faculty_id": candidate.faculty_id,
                    "name": _full_name(
                        candidate.first_name,
                        candidate.last_name,
                        (candidate.name or "").strip() or candidate.faculty_id,
                    ),
                    "department": candidate.department or "",
                    "title": candidate.title or "",
                    "email": candidate.email or "",
                    "score": score,
                    "reasons": reasons[:3],
                    "sample_keywords": shared_keywords[:5],
                }
            )

    suggestions.sort(key=lambda item: item["score"], reverse=True)
    return Response({"suggestions": suggestions[:10]})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approve_faculty_suggestion(request, external_faculty_id):
    internal = _get_request_faculty(request.user, create_if_missing=True)
    if not internal:
        return Response(
            {"detail": "Faculty profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND,
        )

    external = Faculty.objects.filter(id=external_faculty_id, user__isnull=True).first()
    if not external:
        return Response(
            {"detail": "Suggested faculty not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if external.pk == internal.pk:
        return Response(
            {"detail": "Cannot absorb your own faculty profile."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        summary = _absorb_external_faculty(internal, external)
        FacultySuggestionDecision.objects.update_or_create(
            reviewer=internal,
            external_faculty=external,
            defaults={"decision": "approved"},
        )

    serializer = FacultyProfileSerializer(internal)
    return Response(
        {
            "message": "Suggested faculty absorbed into your profile.",
            "merged": summary,
            "faculty": serializer.data,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def faculty_suggestion_preview(request, external_faculty_id):
    internal = _get_request_faculty(request.user, create_if_missing=True)
    if not internal:
        return Response(
            {"detail": "Faculty profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND,
        )

    external = (
        Faculty.objects.filter(id=external_faculty_id, user__isnull=True, profile_visibility=True)
        .prefetch_related("papers", "projects", "patents")
        .first()
    )
    if not external:
        return Response(
            {"detail": "Suggested faculty not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(_external_faculty_preview_payload(external), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reject_faculty_suggestion(request, external_faculty_id):
    internal = _get_request_faculty(request.user, create_if_missing=True)
    if not internal:
        return Response(
            {"detail": "Faculty profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND,
        )

    external = Faculty.objects.filter(id=external_faculty_id, user__isnull=True).first()
    if not external:
        return Response(
            {"detail": "Suggested faculty not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    FacultySuggestionDecision.objects.update_or_create(
        reviewer=internal,
        external_faculty=external,
        defaults={"decision": "rejected"},
    )

    return Response(
        {"message": "Suggestion rejected and will be hidden from future suggestions."},
        status=status.HTTP_200_OK,
    )


class FacultyPhotoUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        faculty = _get_request_faculty(request.user, create_if_missing=True)
        if not faculty:
            return Response(
                {"detail": "Faculty profile not found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )
        photo = request.data.get("photo")

        if not photo:
            return Response({"error": "No photo uploaded"}, status=400)

        faculty.photo = photo
        faculty.save()

        return Response({
            "message": "Photo updated",
            "photo": request.build_absolute_uri(faculty.photo.url)
        })

class FacultyUploadCVPapers(APIView):
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        faculty = _get_request_faculty(request.user, create_if_missing=True)
        if not faculty:
            return Response(
                {"detail": "Faculty profile not found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )
        file = request.FILES.get("file")

        if not file:
            return Response({"error": "No PDF uploaded"}, status=400)
        try:
            with pdfplumber.open(file) as pdf:
                full_text = "\n".join([page.extract_text() or "" for page in pdf.pages])
        except Exception as e:
            return Response({"error": f"PDF extract error: {str(e)}"}, status=400)
        entries = []

        doi_pattern = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"

        for line in full_text.split("\n"):
            doi_match = re.search(doi_pattern, line)
            if doi_match:
                entries.append({
                    "title": line.replace(doi_match.group(), "").strip(),
                    "doi": doi_match.group()
                })

        created = []
        for item in entries:
            paper, _ = Paper.objects.get_or_create(
                doi=item["doi"],
                defaults={"title": item["title"] or "Untitled Paper"}
            )
            paper.authors.add(faculty)
            created.append({"title": paper.title, "doi": paper.doi})

        return Response({
            "message": "PDF processed",
            "papers_found": len(created),
            "papers": created
        })
