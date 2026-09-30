"""Durable Google revocation is distinct from transient provider failure."""
import unittest
from unittest.mock import Mock

from credentials import _TOKEN_CACHE, mint_access_token
from errors import ProxyError


class CredentialErrorTests(unittest.TestCase):
    def test_revoked_refresh_has_safe_specific_error_and_no_cached_token(self):
        _TOKEN_CACHE.clear()
        self.addCleanup(_TOKEN_CACHE.clear)
        http = Mock()
        http.post.return_value.status_code = 400
        http.post.return_value.json.return_value = {
            "error": "invalid_grant", "error_description": "private provider details"
        }
        with self.assertRaises(ProxyError) as failed:
            mint_access_token("user@example.com", {"type": "oauth_refresh", "client_id": "client",
                "client_secret": "secret", "refresh_token": "revoked"}, http)
        self.assertEqual(401, failed.exception.status_code)
        self.assertEqual("refresh_revoked", failed.exception.message)
        self.assertNotIn("private", str(failed.exception))
        self.assertNotIn("user@example.com", _TOKEN_CACHE)

    def test_unavailable_provider_is_not_reported_as_revoked(self):
        _TOKEN_CACHE.clear()
        self.addCleanup(_TOKEN_CACHE.clear)
        http = Mock()
        http.post.return_value.status_code = 503
        http.post.return_value.json.return_value = {"error": "unavailable"}
        with self.assertRaises(ProxyError) as failed:
            mint_access_token("user@example.com", {"type": "oauth_refresh", "client_id": "client",
                "client_secret": "secret", "refresh_token": "refresh"}, http)
        self.assertEqual(502, failed.exception.status_code)


if __name__ == "__main__":
    unittest.main()
