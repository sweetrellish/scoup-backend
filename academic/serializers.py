"""Serializers for SCOUP academic APIs."""

import uuid
from rest_framework import serializers

from academic.models import (
    ContactSettings,
    ContactTeamMember,
    Faculty,
    Paper,
    Patent,
    Project,
)


class EmptyStringToNoneDateField(serializers.DateField):
    def to_internal_value(self, value):
        if value in ("", None):
            return None
        return super().to_internal_value(value)

class FacultySerializer(serializers.ModelSerializer):
    class Meta:
        model = Faculty
        fields = "__all__"
        read_only_fields = ["user"]

class FacultyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Faculty
        fields = "__all__"
        read_only_fields = ["user", "faculty_id", "created_at", "updated_at"]

class PaperSerializer(serializers.ModelSerializer):
    doi = serializers.CharField(required=False, allow_blank=True)
    authors = serializers.CharField(write_only=True, required=False, allow_blank=True)
    year = serializers.CharField(write_only=True, required=False, allow_blank=True)
    status = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Paper
        fields = "__all__"
        read_only_fields = ("authors",)

    def create(self, validated_data):
        validated_data.pop("authors", None)
        year = validated_data.pop("year", None)
        validated_data.pop("status", None)

        if not validated_data.get("doi"):
            validated_data["doi"] = f"manual:{uuid.uuid4()}"

        if year and not validated_data.get("date_published"):
            try:
                parsed_year = int(str(year))
                validated_data["date_published"] = f"{parsed_year}-01-01"
            except (TypeError, ValueError):
                pass

        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("authors", None)
        year = validated_data.pop("year", None)
        validated_data.pop("status", None)

        if year:
            try:
                parsed_year = int(str(year))
                validated_data["date_published"] = f"{parsed_year}-01-01"
            except (TypeError, ValueError):
                pass

        return super().update(instance, validated_data)

class ProjectSerializer(serializers.ModelSerializer):
    start_date = EmptyStringToNoneDateField(required=False, allow_null=True)
    end_date = EmptyStringToNoneDateField(required=False, allow_null=True)
    collaborators = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )
    funding_amount = serializers.CharField(
        required=False, allow_blank=True, write_only=True
    )
    outcomes = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Project
        fields = '__all__'
        read_only_fields = ('faculty',)

    def create(self, validated_data):
        validated_data.pop("collaborators", None)
        validated_data.pop("funding_amount", None)
        validated_data.pop("outcomes", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("collaborators", None)
        validated_data.pop("funding_amount", None)
        validated_data.pop("outcomes", None)
        return super().update(instance, validated_data)

class PatentSerializer(serializers.ModelSerializer):
    patent_number = serializers.CharField(required=False, allow_blank=True)
    filing_date = EmptyStringToNoneDateField(required=False, allow_null=True)
    issue_date = EmptyStringToNoneDateField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_blank=True, write_only=True)
    inventors = serializers.CharField(required=False, allow_blank=True, write_only=True)
    application_number = serializers.CharField(
        required=False, allow_blank=True, write_only=True
    )
    assignee = serializers.CharField(required=False, allow_blank=True, write_only=True)
    keywords = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = Patent
        fields = '__all__'
        read_only_fields = ('faculty',)

    def create(self, validated_data):
        validated_data.pop("status", None)
        validated_data.pop("inventors", None)
        validated_data.pop("application_number", None)
        validated_data.pop("assignee", None)
        validated_data.pop("keywords", None)

        if not validated_data.get("patent_number"):
            validated_data["patent_number"] = f"PN-{uuid.uuid4().hex[:12].upper()}"

        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("status", None)
        validated_data.pop("inventors", None)
        validated_data.pop("application_number", None)
        validated_data.pop("assignee", None)
        validated_data.pop("keywords", None)
        return super().update(instance, validated_data)


class ContactTeamMemberSerializer(serializers.ModelSerializer):
    """Contact-page team member.

    Field names are passed through unchanged - the admin editor PATCHes exactly
    these keys, so any renaming here would silently drop edits.
    """

    class Meta:
        model = ContactTeamMember
        fields = [
            "id",
            "name",
            "role",
            "description",
            "email",
            "linkedin_url",
            "photo",
            "order",
            "is_visible",
        ]
        # Photo is set through the dedicated upload endpoint, not this serializer,
        # because the editor sends it as multipart on a separate request.
        read_only_fields = ["id", "photo"]


class ContactSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactSettings
        fields = [
            "general_email",
            "support_email",
            "github_url",
            "backend_github_url",
            "linkedin_url",
            "documentation_url",
            "api_documentation_url",
            "documentation_links",
            "address_line_1",
            "address_line_2",
            "address_line_3",
        ]

    def validate_documentation_links(self, value):
        """Only accept the {title, description, url} card shape the docs page renders."""
        if not isinstance(value, list):
            raise serializers.ValidationError("Expected a list of documentation cards.")
        cleaned = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    f"Card {index + 1} must be an object with title, description and url."
                )
            cleaned.append(
                {
                    "title": str(item.get("title", ""))[:255],
                    "description": str(item.get("description", ""))[:1000],
                    "url": str(item.get("url", ""))[:500],
                }
            )
        return cleaned
