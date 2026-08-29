"""Contact-page endpoints: public team/settings feeds plus the admin editor's CRUD.

The frontend for all of this already existed (`src/components/Contact.tsx`,
`src/components/Documentation.tsx`, `src/components/admin/ContactPageEditor.tsx`
and `contactAPI` in `src/utils/api.ts`); nothing served it, so `/contact` and
`/docs` 404'd on every load. These views match the field names those components
already send and read - see `ContactTeamMemberSerializer` for the pass-through.
"""

import logging

from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from .models import ContactSettings, ContactTeamMember
from .serializers import ContactSettingsSerializer, ContactTeamMemberSerializer

logger = logging.getLogger(__name__)


def _serialize_member(member, request):
    """Serialize one member, with the photo as an absolute URL the browser can load."""
    data = ContactTeamMemberSerializer(member).data
    photo_url = ""
    if member.photo:
        try:
            photo_url = request.build_absolute_uri(member.photo.url)
        except Exception:
            # A missing file or misconfigured storage must not take down the page.
            photo_url = ""
    data["photo"] = photo_url or None
    return data


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def contact_team(request):
    """Visible team members, in admin-chosen display order."""
    members = ContactTeamMember.objects.filter(is_visible=True)
    return Response([_serialize_member(m, request) for m in members])


@api_view(["GET"])
@permission_classes([AllowAny])
def contact_settings(request):
    """Contact/docs page settings. Returns a blank row rather than 404 on a fresh install."""
    settings_row = ContactSettings.load()
    return Response(ContactSettingsSerializer(settings_row).data)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def admin_contact_team(request):
    """List every member (hidden ones included) or create a new one."""
    if request.method == "POST":
        serializer = ContactTeamMemberSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        member = serializer.save()
        return Response(_serialize_member(member, request), status=status.HTTP_201_CREATED)

    members = ContactTeamMember.objects.all()
    return Response([_serialize_member(m, request) for m in members])


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAdminUser])
def admin_contact_team_detail(request, pk):
    try:
        member = ContactTeamMember.objects.get(pk=pk)
    except ContactTeamMember.DoesNotExist:
        return Response({"detail": "Team member not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        serializer = ContactTeamMemberSerializer(member, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        member = serializer.save()

    return Response(_serialize_member(member, request))


@api_view(["PATCH"])
@permission_classes([IsAdminUser])
def admin_contact_settings(request):
    settings_row = ContactSettings.load()
    serializer = ContactSettingsSerializer(settings_row, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def admin_contact_team_photo(request, pk):
    """Photo upload for one member; `contactAPI.adminUploadPhoto` posts a `photo` file part."""
    try:
        member = ContactTeamMember.objects.get(pk=pk)
    except ContactTeamMember.DoesNotExist:
        return Response({"detail": "Team member not found."}, status=status.HTTP_404_NOT_FOUND)

    photo = request.data.get("photo")
    if not photo:
        return Response({"detail": "No photo uploaded."}, status=status.HTTP_400_BAD_REQUEST)

    member.photo = photo
    member.save(update_fields=["photo", "updated_at"])
    return Response(_serialize_member(member, request))
