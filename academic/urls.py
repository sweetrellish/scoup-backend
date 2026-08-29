from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views
from .public_profile_views import faculty_public_profile
from .auth import EmailOrUsernameTokenObtainPairView
from .admin_views import (
    admin_me,
    admin_stats,
    admin_faculty_list,
    admin_faculty_detail,
    admin_faculty_approve,
    admin_faculty_reject,
    admin_faculty_bulk_action,
    admin_faculty_message,
    admin_inquiries,
    admin_inquiry_detail,
    admin_audit_log,
)
from .reference_views import (
    institutions_list,
    facilities_list,
)
from .network_views import (
    network_discovery,
    network_inquire,
    faculty_inquiries,
    faculty_inquiry_detail,
)
from .views import (
    approve_faculty_suggestion,
    categories_list,
    category_detail,
    query_expansions,
    FacultyListCreateView,
    FacultyDetailView,
    faculty_me,
    faculty_me_suggestions,
    faculty_suggestion_preview,
    public_search_data,
    semantic_paper_search,
    reject_faculty_suggestion,
    PaperListCreateView,
    PaperDetailView,
    ProjectListCreateView,
    ProjectDetailView,
    PatentListCreateView,
    PatentDetailView,
    FacultyPhotoUploadView,
    FacultyUploadCVPapers,
)
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)
urlpatterns = [
    path('', views.home, name='home'),
    path("public/search-data/", public_search_data, name="public-search-data"),
    path("search/", semantic_paper_search, name="paper-search"),
    path("semantic/papers/", semantic_paper_search, name="semantic-paper-search"),
    path("categories/", categories_list, name="categories-list"),
    path("query-expansions/", query_expansions, name="query-expansions"),
    path("admin/me/", admin_me, name="admin-me"),
    path("admin/stats/", admin_stats, name="admin-stats"),
    path("admin/audit-log/", admin_audit_log, name="admin-audit-log"),
    path("admin/faculty/", admin_faculty_list, name="admin-faculty-list"),
    path("admin/faculty/bulk-action/", admin_faculty_bulk_action, name="admin-faculty-bulk"),
    path("admin/faculty/<int:pk>/", admin_faculty_detail, name="admin-faculty-detail"),
    path("admin/faculty/<int:pk>/approve/", admin_faculty_approve, name="admin-faculty-approve"),
    path("admin/faculty/<int:pk>/reject/", admin_faculty_reject, name="admin-faculty-reject"),
    path("admin/faculty/<int:pk>/message/", admin_faculty_message, name="admin-faculty-message"),
    path("admin/inquiries/", admin_inquiries, name="admin-inquiries"),
    path("admin/inquiries/<int:pk>/", admin_inquiry_detail, name="admin-inquiry-detail"),
    path("institutions/", institutions_list, name="institutions-list"),
    path("facilities/", facilities_list, name="facilities-list"),
    path("network/discovery/", network_discovery, name="network-discovery"),
    path("network/inquire/", network_inquire, name="network-inquire"),
    path("faculty/inquiries/", faculty_inquiries, name="faculty-inquiries"),
    path("faculty/inquiries/<int:pk>/", faculty_inquiry_detail, name="faculty-inquiry-detail"),
    path("categories/<str:category>/", category_detail, name="category-detail"),

    path('faculty/', FacultyListCreateView.as_view(), name='faculty-list'),
    path('faculty/<int:pk>/', FacultyDetailView.as_view(), name='faculty-detail'),
    path('faculty/<int:pk>/public/', faculty_public_profile, name='faculty-public-profile'),
    path('faculty/me/', faculty_me, name='faculty_me'),
    path("faculty/me/suggestions/", faculty_me_suggestions, name="faculty-me-suggestions"),
    path(
        "faculty/me/suggestions/<int:external_faculty_id>/approve/",
        approve_faculty_suggestion,
        name="faculty-suggestion-approve",
    ),
    path(
        "faculty/me/suggestions/<int:external_faculty_id>/preview/",
        faculty_suggestion_preview,
        name="faculty-suggestion-preview",
    ),
    path(
        "faculty/me/suggestions/<int:external_faculty_id>/reject/",
        reject_faculty_suggestion,
        name="faculty-suggestion-reject",
    ),
    path("papers/", PaperListCreateView.as_view(), name="paper-list"),
    path("papers/<int:pk>/", PaperDetailView.as_view(), name="paper-detail"),
    path("projects/", ProjectListCreateView.as_view(), name="project-list"),
    path("projects/<int:pk>/", ProjectDetailView.as_view(), name="project-detail"),
    path("patents/", PatentListCreateView.as_view(), name="patent-list"),
    path("patents/<int:pk>/", PatentDetailView.as_view(), name="patent-detail"),
    path("faculty/signup/", views.faculty_signup, name="faculty_signup"),
    path("token/", EmailOrUsernameTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("faculty/upload-photo/", FacultyPhotoUploadView.as_view()),
    path("faculty/upload-cv-papers/", FacultyUploadCVPapers.as_view(), name="upload-cv-papers"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
