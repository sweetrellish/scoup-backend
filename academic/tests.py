"""Test suite for academic app.

Focused on the admin faculty review queue: the 126 records left pending by
`import_su_directory` when it refused to trust a first-initial-only match.
Runs against Django's throwaway test database; db.sqlite3 is never touched.
"""

import json
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from academic.directory_match import (
    building_for_room,
    load_directory,
    match_directory,
    normalize,
    resolve_school,
)
from academic.models import Faculty, Paper


class DirectoryMatchTests(TestCase):
    """The evidence layer that explains why a record is pending."""

    def test_directory_cache_is_present(self):
        rows, by_last = load_directory()
        self.assertGreater(len(rows), 1000, "run `manage.py export_su_directory --apply`")
        self.assertIn("kim", by_last)

    def test_exact_match_is_verification_grade(self):
        result = match_directory(normalize("Yun-Kyoung'Gail'"), "kim")
        self.assertEqual(result["match_type"], "exact")
        self.assertEqual(result["best_match"]["department"], "Management Department")

    def test_initial_only_match_is_held_for_review(self):
        # "Yun Kyoung Kim" in the paper data vs "Yun-Kyoung'Gail' Kim" in the directory.
        result = match_directory(normalize("Yun Kyoung"), "kim")
        self.assertEqual(result["match_type"], "initial")
        self.assertIsNotNone(result["best_match"])
        self.assertIn("initial", result["reason"].lower())

    def test_ambiguous_match_yields_no_best_match(self):
        # Jennifer Martin and Joni Martin both share the surname and initial "J".
        result = match_directory("joel", "martin")
        self.assertEqual(result["match_type"], "ambiguous")
        self.assertIsNone(
            result["best_match"], "an ambiguous surname must never resolve to a guess"
        )
        self.assertGreater(len(result["candidates"]), 1)

    def test_unknown_surname_is_unmatched(self):
        result = match_directory("xavier", "zzzznotapersonzzzz")
        self.assertEqual(result["match_type"], "unmatched")
        self.assertEqual(result["candidates"], [])

    def test_room_resolves_to_building(self):
        self.assertEqual(building_for_room("PH303"), "Perdue Hall")
        self.assertEqual(building_for_room("AC262"), "Academic Commons")

    def test_unknown_room_prefix_is_not_guessed(self):
        self.assertEqual(building_for_room("ZZ100"), "")
        self.assertEqual(building_for_room(""), "")

    def test_school_resolution_handles_source_spelling_differences(self):
        self.assertEqual(resolve_school("Management Department"), "Perdue School of Business")
        self.assertEqual(
            resolve_school("Leadership&Literacy Studies"), "Seidel School of Education"
        )
        self.assertIsNone(resolve_school("Department That Does Not Exist"))


class AdminReviewQueueTests(TestCase):
    """End-to-end: view, approve and reject a pending faculty record."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="queue-admin", password="test-pass-1234", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

        # Mirrors a real demoted record: initial-only directory match, fields cleared.
        self.initial_match = Faculty.objects.create(
            faculty_id="TEST-KIM",
            name="Yun Kyoung Kim",
            first_name="Yun Kyoung",
            last_name="Kim",
            review_status="pending",
            directory_verified=False,
        )
        self.ambiguous = Faculty.objects.create(
            faculty_id="TEST-MARTIN",
            name="Joel Martin",
            first_name="Joel",
            last_name="Martin",
            review_status="pending",
            directory_verified=False,
        )

    def test_anonymous_cannot_read_the_queue(self):
        self.assertEqual(APIClient().get("/api/admin/faculty/?status=pending").status_code, 401)

    def test_non_staff_cannot_read_the_queue(self):
        user = User.objects.create_user(username="plain", password="test-pass-1234")
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.get("/api/admin/faculty/?status=pending").status_code, 403)

    def test_pending_queue_returns_only_pending_records_with_evidence(self):
        response = self.client.get("/api/admin/faculty/?status=pending")
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual({r["review_status"] for r in rows}, {"pending"})

        by_name = {r["name"]: r for r in rows}
        evidence = by_name["Yun Kyoung Kim"]["review_evidence"]
        self.assertEqual(evidence["match_type"], "initial")
        self.assertEqual(evidence["best_match"]["department"], "Management Department")
        self.assertEqual(evidence["best_match"]["building"], "Perdue Hall")
        self.assertTrue(evidence["reason"])

        self.assertIsNone(by_name["Joel Martin"]["review_evidence"]["best_match"])

    def test_pending_filter_alias_matches_status_filter(self):
        by_flag = self.client.get("/api/admin/faculty/?pending=true").json()
        by_status = self.client.get("/api/admin/faculty/?status=pending").json()
        self.assertEqual([r["id"] for r in by_flag], [r["id"] for r in by_status])

    def test_approve_applying_directory_match_fills_the_cleared_fields(self):
        response = self.client.post(
            f"/api/admin/faculty/{self.initial_match.pk}/approve/",
            data=json.dumps({"apply_directory_match": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("department", response.json()["applied_fields"])

        member = Faculty.objects.get(pk=self.initial_match.pk)
        self.assertEqual(member.review_status, "approved")
        self.assertTrue(member.is_approved)
        self.assertTrue(member.directory_verified)
        self.assertEqual(member.department, "Management Department")
        self.assertEqual(member.title, "Assistant Professor")
        self.assertEqual(member.school, "Perdue School of Business")
        self.assertIn("queue-admin", member.review_note)

    def test_approving_without_applying_leaves_directory_fields_alone(self):
        response = self.client.post(
            f"/api/admin/faculty/{self.initial_match.pk}/approve/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        member = Faculty.objects.get(pk=self.initial_match.pk)
        self.assertEqual(member.review_status, "approved")
        self.assertFalse(member.directory_verified)
        self.assertFalse(member.department)

    def test_cannot_apply_a_directory_match_that_is_ambiguous(self):
        response = self.client.post(
            f"/api/admin/faculty/{self.ambiguous.pk}/approve/",
            data=json.dumps({"apply_directory_match": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        member = Faculty.objects.get(pk=self.ambiguous.pk)
        self.assertEqual(member.review_status, "pending", "a failed apply must not approve")
        self.assertFalse(member.department)

    def test_reject_records_the_reason(self):
        response = self.client.post(
            f"/api/admin/faculty/{self.ambiguous.pk}/reject/",
            data=json.dumps({"reason": "External co-author, not SU faculty"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        member = Faculty.objects.get(pk=self.ambiguous.pk)
        self.assertEqual(member.review_status, "rejected")
        self.assertFalse(member.is_approved)
        self.assertFalse(member.profile_visibility)
        self.assertIn("External co-author", member.review_note)

    def test_bulk_reject_clears_both_records_from_the_queue(self):
        response = self.client.post(
            "/api/admin/faculty/bulk-action/",
            data=json.dumps(
                {
                    "action": "reject",
                    "ids": [self.initial_match.pk, self.ambiguous.pk],
                    "reason": "Bulk triage",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)
        self.assertEqual(Faculty.objects.filter(review_status="pending").count(), 0)

    def test_invalid_bulk_action_is_rejected(self):
        response = self.client.post(
            "/api/admin/faculty/bulk-action/",
            data=json.dumps({"action": "delete", "ids": [self.ambiguous.pk]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class ReferenceEndpointTests(TestCase):
    """Institutions and Facilities: file-backed reference data, no models."""

    def setUp(self):
        self.client = APIClient()

    def test_institutions_are_public_and_cleaned(self):
        response = self.client.get("/api/institutions/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(body["total"], 50)

        names = [r["name"] for r in body["results"]]
        # Name bleed and truncation must not survive into the shipped list.
        self.assertNotIn("Dean J. Kotlowski Salisbury University", names)
        self.assertNotIn("University of Delaware. He", names)
        self.assertNotIn("Mason University", names)
        self.assertIn("Salisbury University", names)

    def test_host_institution_is_flagged_and_shows_what_was_merged(self):
        body = self.client.get("/api/institutions/").json()
        host = next(r for r in body["results"] if r["name"] == "Salisbury University")
        self.assertTrue(host["isHost"])
        self.assertIn("Dean J. Kotlowski Salisbury University", host["mergedFrom"])

    def test_institutions_search_and_host_exclusion(self):
        body = self.client.get("/api/institutions/?q=vanderbilt").json()
        self.assertEqual([r["name"] for r in body["results"]], ["Vanderbilt University"])

        body = self.client.get("/api/institutions/?exclude_host=true").json()
        self.assertNotIn("Salisbury University", [r["name"] for r in body["results"]])

    def test_facilities_join_rooms_to_buildings(self):
        response = self.client.get("/api/facilities/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(body["summary"]["buildingCodes"], 100)

        by_name = {r["name"]: r for r in body["results"]}
        henson = by_name["Henson Science Hall"]
        self.assertIn("HS", henson["codes"])

    def test_facilities_do_not_merge_near_miss_names(self):
        """'Devilbiss Science Hall' and 'Devilbiss Hall' stay distinct by design."""
        body = self.client.get("/api/facilities/").json()
        by_name = {r["name"]: r for r in body["results"]}
        self.assertIn("Devilbiss Science Hall", by_name)
        self.assertFalse(by_name["Devilbiss Science Hall"]["onFacilitiesPage"])

    def test_facilities_occupied_filter(self):
        body = self.client.get("/api/facilities/?occupied=true").json()
        self.assertTrue(all(r["facultyCount"] > 0 for r in body["results"]))


class SearchFilterTests(TestCase):
    """Optional filters on /api/search/ narrow results without re-ranking them."""

    def setUp(self):
        self.client = APIClient()
        rows = [
            ("Machine learning for coastal mapping", 2024, 5, "Journal of Coastal Research"),
            ("Machine learning in clinical oncology", 2005, 120, "Annals of Oncology"),
            ("Machine learning and student outcomes", 2021, 0, "Education Review"),
        ]
        for i, (title, year, citations, journal) in enumerate(rows):
            Paper.objects.create(
                title=title,
                doi=f"10.0000/test-{i}",
                journal=journal,
                tc_count=citations,
                date_published=date(year, 6, 1),
                abstract="Machine learning applied to a test corpus.",
            )

    def _search(self, query):
        response = self.client.get(f"/api/search/?q=machine+learning&{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_unfiltered_search_reports_no_active_filters(self):
        body = self._search("limit=50")
        self.assertEqual(body["filters"], {"sort": "relevance"})
        self.assertGreaterEqual(body["count"], 3)

    def test_year_range_filter(self):
        years = [r["year"] for r in self._search("limit=50&year_min=2020")["results"]]
        self.assertTrue(years)
        self.assertTrue(all(y >= 2020 for y in years), years)

        years = [r["year"] for r in self._search("limit=50&year_max=2010")["results"]]
        self.assertTrue(all(y <= 2010 for y in years), years)

    def test_min_citations_filter(self):
        results = self._search("limit=50&min_citations=100")["results"]
        self.assertTrue(results)
        self.assertTrue(all(r["citations"] >= 100 for r in results))

    def test_journal_filter_is_a_substring_match(self):
        results = self._search("limit=50&journal=oncology")["results"]
        self.assertTrue(results)
        self.assertTrue(all("oncology" in r["journal"].lower() for r in results))

    def test_sort_by_citations_reorders_results(self):
        citations = [r["citations"] for r in self._search("limit=50&sort=citations")["results"]]
        self.assertEqual(citations, sorted(citations, reverse=True))

    def test_sort_by_year_reorders_results(self):
        years = [r["year"] for r in self._search("limit=50&sort=year")["results"]]
        self.assertEqual(years, sorted(years, reverse=True))

    def test_unparseable_filters_are_ignored_rather_than_failing(self):
        body = self._search("limit=50&year_min=abc&min_citations=&sort=bogus")
        self.assertEqual(body["filters"], {"sort": "relevance"})
