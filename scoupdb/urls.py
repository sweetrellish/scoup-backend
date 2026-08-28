from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from django.views.generic import TemplateView
from academic import views

urlpatterns = [
    path("", views.home),
    path("admin/", admin.site.urls),
    path("api/", include("academic.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Nginx serves the SPA from dist/, so Django must not swallow unmatched API routes
# into a template fallback (that turned real 404s into TemplateDoesNotExist 500s).
urlpatterns += [
    re_path(r"^(?!api/|admin/|media/|static/)(?P<resource>.*)$",
            TemplateView.as_view(template_name="index.html")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
