"""Calendar consent, service ceiling, and read-only verification contracts."""
import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import onboarding_worker as worker
from onboarding_google import GoogleClient
from onboarding_services import DEFAULT_SERVICES, requested_scopes, service_policy, verifier_policy
from onboarding_store import WorkflowError
from policy import _compose_policy_documents, _evaluate_policy, _normalize_policy_document
import test_onboarding_lifecycle as lifecycle


class CalendarContracts(unittest.TestCase):
    def setUp(self):
        lifecycle.AccountLifecycleTests.setUp(self)

    def test_new_account_defaults_to_all_three_services(self):
        account = self.service.save_account("owner", "new@example.com", {})
        self.assertEqual(["gmail", "drive", "calendar"], account["requested_services"])
        self.assertEqual(account["requested_services"],
            self.store.get("ACCOUNT#new@example.com")["requested_services"])

    def test_explicit_service_choices_survive_saving_without_services(self):
        for services in (["gmail", "drive"], ["calendar"]):
            with self.subTest(services=services):
                expected = services
                account = self.service.save_account("owner", "new@example.com", {"services": services})
                self.assertEqual(expected, account["requested_services"])
                account = self.service.save_account("owner", "new@example.com", {})
                self.assertEqual(expected, account["requested_services"])

    def decision(self, document, method, host, path):
        policy = _compose_policy_documents([_normalize_policy_document(document, source="default", source_rank=0)])
        return _evaluate_policy(policy, {"method": method, "host": host, "path": path,
            "account_email": "jay@example.com", "account_domain": "example.com", "query_keys": [],
            "request_size_bytes": 0})["effect"]

    def test_calendar_only_enrollment_requires_its_api_and_requests_only_calendar(self):
        self.service.save_account("owner", "jay@example.com", {"services": ["calendar"]})
        with self.assertRaises(WorkflowError) as failed:
            self.service.enroll("owner", "jay@example.com", "unreviewed-calendar", "session", {})
        self.assertEqual("application_setup_required", failed.exception.code)
        revision = self.store.get("CLIENT#gogcli-proxy")["revision"]
        self.service.review_application( {"clientRevision": revision,
            "apiConfirmed": {"calendar": True}})
        self.service.enroll("owner", "jay@example.com", "calendar", "session", {})
        self.assertEqual(["calendar"], self.google.authorization_url.call_args.args[-1])
        google = GoogleClient(Mock())
        url = google.authorization_url({"client_id": "client"}, self.service.callback_url,
            "state", "verifier", "nonce", "jay@example.com", ["calendar"])
        self.assertEqual(["openid", "email", "https://www.googleapis.com/auth/calendar"],
            parse_qs(urlsplit(url).query)["scope"][0].split())
        self.assertEqual(["gmail", "drive", "calendar"], DEFAULT_SERVICES)

    def test_calendar_policy_allows_calendar_writes_and_excludes_other_services(self):
        document = service_policy(["calendar"])
        for host in ("www.googleapis.com", "calendar.googleapis.com"):
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(host=host, method=method):
                    self.assertEqual("allow", self.decision(document, method, host, "/calendar/v3/calendars/primary/events"))
        for host, path in (("gmail.googleapis.com", "/calendar/v3/calendars/primary/events"),
                           ("www.googleapis.com", "/drive/v3/files"),
                           ("www.googleapis.com", "/gmail/v1/users/me/messages"),
                           ("www.googleapis.com", "/calendar/v30/calendars")):
            with self.subTest(host=host, path=path):
                self.assertEqual("deny", self.decision(document, "GET", host, path))

    def test_calendar_candidate_checks_at_most_one_calendar_id_without_events(self):
        for payload in ({"items": []}, {"items": [{"id": "shared-calendar@group.calendar.google.com"}]}):
            with self.subTest(payload=payload):
                http = Mock()
                http.post.return_value = Mock(status_code=200, json=lambda: {"access_token": "fresh"})
                http.get.return_value = Mock(status_code=200, json=lambda: payload)
                GoogleClient(http).validate_candidate("jay@example.com", {"client_id": "client",
                    "client_secret": "secret", "refresh_token": "refresh", "scopes": requested_scopes(["calendar"])}, ["calendar"])
                http.get.assert_called_once()
                call = http.get.call_args
                self.assertEqual("https://www.googleapis.com/calendar/v3/users/me/calendarList", call.args[0])
                self.assertEqual({"maxResults": 1, "fields": "items/id"}, call.kwargs["params"])
        http.get.return_value = Mock(status_code=200, json=lambda: {"items": "invalid"})
        with self.assertRaises(WorkflowError) as failed:
            GoogleClient(http).validate_candidate("jay@example.com", {"client_id": "client",
                "client_secret": "secret", "refresh_token": "refresh", "scopes": requested_scopes(["calendar"])}, ["calendar"])
        self.assertEqual("invalid_provider_response", failed.exception.code)

    def test_calendar_proxy_verifier_is_read_only_and_does_not_read_event_content(self):
        http = Mock()
        http.get.return_value = Mock(status_code=200, json=lambda: {"items": []})
        with patch.dict(os.environ, {"PROXY_BASE_URL": "https://api.example/prd/", "PROXY_API_KEY_ID": "key"}), \
             patch.object(worker, "aws_client") as api, patch.object(worker, "signed_headers", return_value={}):
            api.return_value.get_api_key.return_value = {"value": "fixture-key"}
            result = worker.verify_proxy("jay@example.com", http, ["calendar"])
        self.assertEqual({"calendar": {"status": "succeeded"}}, result)
        http.get.assert_called_once()
        url = urlsplit(http.get.call_args.args[0])
        self.assertTrue(url.path.endswith("/calendar/v3/users/me/calendarList"))
        self.assertEqual({"maxResults": ["1"], "fields": ["items/id"]}, parse_qs(url.query))
        document = verifier_policy()
        for method, path, expected in (("GET", "/calendar/v3/users/me/calendarList", "allow"),
                ("POST", "/calendar/v3/users/me/calendarList", "deny"),
                ("GET", "/calendar/v3/calendars/primary/events", "deny")):
            with self.subTest(method=method, path=path):
                self.assertEqual(expected, self.decision(document, method, "www.googleapis.com", path))


if __name__ == "__main__":
    unittest.main()
