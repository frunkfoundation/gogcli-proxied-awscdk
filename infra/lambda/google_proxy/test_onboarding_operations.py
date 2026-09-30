"""Google consent and selected-service checks at the public provider boundary."""
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from onboarding_google import GoogleClient, check_response, validate_web_client
from onboarding_services import requested_scopes
from onboarding_store import WorkflowError


class GoogleContractTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.google = GoogleClient(self.http)
        self.client = {"client_id": "client", "client_secret": "secret", "project_id": "google-project"}
        self.callback = "https://app.example/onboarding/oauth/google/callback"
        self.credentials = {"client_id": "client", "client_secret": "secret", "refresh_token": "refresh",
                            "scopes": requested_scopes(["drive"])}

    def test_desktop_client_and_missing_exact_callback_are_rejected(self):
        for value in ({"installed": self.client}, {"web": self.client},
                      {"web": {**self.client, "redirect_uris": ["https://different.example"]}}):
            with self.subTest(value=value), self.assertRaises(WorkflowError):
                validate_web_client(value, self.callback)
        self.assertEqual(self.client, validate_web_client({"web": {**self.client, "redirect_uris": [self.callback]}}, self.callback))

    def test_drive_only_consent_contains_no_gmail_scope_and_has_pkce(self):
        url = self.google.authorization_url(self.client, self.callback, "state", "verifier", "nonce", "jay@example.com", ["drive"])
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(requested_scopes(["drive"]), query["scope"][0].split())
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual(["offline"], query["access_type"])

    def test_wrong_account_is_distinct_from_missing_service_permission(self):
        tokens = {"id_token": "id", "scope": " ".join(requested_scopes(["gmail"]))}
        claims = {"nonce": "nonce", "email_verified": True, "sub": "subject", "email": "wrong@example.com"}
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
            with self.assertRaises(WorkflowError) as failed:
                self.google.identity(tokens, self.client, "nonce", "jay@example.com", ["gmail"])
        self.assertEqual("identity_mismatch", failed.exception.code)
        claims["email"] = "jay@example.com"
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
            with self.assertRaises(WorkflowError) as failed:
                self.google.identity(tokens, self.client, "nonce", "jay@example.com", ["drive"])
        self.assertEqual("missing_service_permission", failed.exception.code)

    def test_email_scope_alias_is_normalized_without_inventing_service_evidence(self):
        tokens = {"id_token": "id", "scope": "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/drive"}
        claims = {"nonce": "nonce", "email_verified": True, "sub": "subject", "email": "jay@example.com"}
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
            identity = self.google.identity(tokens, self.client, "nonce", "jay@example.com", ["drive"])
        self.assertEqual("subject", identity["subject"])
        self.assertIn("email", identity["granted_scopes"])
        self.assertNotIn("https://www.googleapis.com/auth/gmail.modify", identity["granted_scopes"])

    def test_drive_candidate_proves_a_fresh_refresh_and_empty_bounded_listing(self):
        self.http.post.return_value = Mock(status_code=200, json=lambda: {"access_token": "fresh"})
        self.http.get.side_effect = [Mock(status_code=200, json=lambda: {"user": {"emailAddress": "jay@example.com"}}),
                                     Mock(status_code=200, json=lambda: {"files": []})]
        self.google.validate_candidate("jay@example.com", self.credentials, ["drive"])
        self.assertEqual("refresh_token", self.http.post.call_args.kwargs["data"]["grant_type"])
        self.assertEqual(2, self.http.get.call_count)
        self.assertTrue(all("drive/v3/" in call.args[0] for call in self.http.get.call_args_list))
        self.assertEqual(1, self.http.get.call_args_list[1].kwargs["params"]["pageSize"])
        self.assertEqual("files/id", self.http.get.call_args_list[1].kwargs["params"]["fields"])

    def test_revoked_refresh_is_reported_safely_without_google_response_body(self):
        self.http.post.return_value = Mock(status_code=400, json=lambda: {"error": "invalid_grant", "error_description": "private-value"})
        with self.assertRaises(WorkflowError) as failed:
            self.google.validate_candidate("jay@example.com", self.credentials, ["drive"])
        self.assertEqual("refresh_revoked", failed.exception.code)
        self.assertNotIn("private", str(failed.exception))
        self.http.get.assert_not_called()

    def test_google_api_disabled_and_proxy_permission_denied_remain_distinct(self):
        response = Mock(status_code=403, json=lambda: {"error": {"errors": [{"reason": "accessNotConfigured"}]}})
        with self.assertRaises(WorkflowError) as failed:
            check_response(response, proxy=True)
        self.assertEqual("api_disabled", failed.exception.code)
        response.json = lambda: {"error": "policy_denied"}
        with self.assertRaises(WorkflowError) as failed:
            check_response(response, proxy=True)
        self.assertEqual("permission_blocked", failed.exception.code)


if __name__ == "__main__":
    unittest.main()
