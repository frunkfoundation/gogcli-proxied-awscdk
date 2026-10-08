import base64
import json
import os
import unittest
from unittest.mock import Mock, patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import policy as policy_engine
import proxy_authorization as authorization
import proxy_config
import credentials
import handler


class ProxyContractTests(unittest.TestCase):
    def setUp(self):
        self.account = "agent@example.com"
        self.cfg = {"type": "oauth_refresh", "client_id": "client", "client_secret": "secret", "refresh_token": "refresh"}
        self.http = Mock()
        self.http.post.side_effect = lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"access_token": f"token-{self.http.post.call_count}", "expires_in": 3600})
        self.payload = b"ok"
        self.headers = {"Content-Type": "application/octet-stream"}
        self.http.send.side_effect = lambda *args, **kwargs: Mock(status_code=200, headers=self.headers, iter_content=lambda **kwargs: iter([self.payload]))
        self.env = patch.dict(os.environ, {"GOG_POLICY_ENABLED": "false"})
        self.env.start()
        self.http_patch = patch.object(handler, "_HTTP", self.http)
        self.http_patch.start()
        self.ssm = patch.object(proxy_config, "_ssm_get_json", side_effect=lambda name: {"emails": ["*"]} if "/allow/" in name else self.cfg)
        self.ssm.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.http_patch.stop)
        self.addCleanup(self.ssm.stop)
        credentials._TOKEN_CACHE.clear()

    def invoke(self, context=None, **overrides):
        event = {
            "httpMethod": "GET", "path": "/gmail/v1/users/me/labels",
            "headers": {"X-GOG-Account": self.account, "X-GOG-Google-Host": "gmail.googleapis.com"},
            "requestContext": {"identity": {"userArn": "arn:aws:sts::123456789012:assumed-role/agent/session"}},
        }
        event.update(overrides)
        return handler.handler(event, context)

    def test_binary_response_respects_serialized_lambda_limit(self):
        self.payload = b"x" * 4_800_000
        response = self.invoke()
        self.assertEqual(502, response["statusCode"])
        self.assertLessEqual(len(json.dumps(response).encode()), 6 * 1024 * 1024)

    def test_text_escaping_and_headers_count_toward_response_limit(self):
        for payload, headers in (
            (b'"' * 3_200_000, {"Content-Type": "text/plain"}),
            (b"x" * 3_100_000, {"Content-Type": "text/plain", "X-Large": "y" * 3_200_000}),
        ):
            with self.subTest(headers=list(headers)):
                self.payload, self.headers = payload, headers
                self.assertEqual(502, self.invoke()["statusCode"])

    def test_small_response_preserved(self):
        response = self.invoke()
        self.assertEqual(200, response["statusCode"])
        self.assertTrue(response["isBase64Encoded"])
        self.assertEqual(self.payload, base64.b64decode(response["body"]))

    def test_warm_invocations_reuse_token_per_account(self):
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(1, self.http.post.call_count)
        self.account = "other@example.com"
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(2, self.http.post.call_count)
        self.account = "agent@example.com"
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual("Bearer token-1", self.http.send.call_args.args[0].headers["Authorization"])

    def test_config_rotation_invalidates_cached_token(self):
        self.invoke()
        self.cfg["refresh_token"] = "rotated"
        self.invoke()
        self.assertEqual(2, self.http.post.call_count)
        self.assertEqual("rotated", self.http.post.call_args.kwargs["data"]["refresh_token"])

    def test_near_expiry_refreshes_token(self):
        with patch("time.time", return_value=1000):
            self.invoke()
        with patch("time.time", return_value=4539):
            self.invoke()
        self.assertEqual(1, self.http.post.call_count)
        with patch("time.time", return_value=4540):
            self.invoke()
        self.assertEqual(2, self.http.post.call_count)

    def test_token_without_expiry_is_not_cached(self):
        self.http.post.side_effect = None
        self.http.post.return_value = Mock(status_code=200, json=lambda: {"access_token": "token"})
        self.invoke()
        self.invoke()
        self.assertEqual(2, self.http.post.call_count)

    def test_token_mint_and_forward_share_remaining_deadline(self):
        clock = [100.0]
        original_post = self.http.post.side_effect

        def slow_token(*args, **kwargs):
            clock[0] = 120.0
            return original_post(*args, **kwargs)

        self.http.post.side_effect = slow_token
        with patch("time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(200, self.invoke()["statusCode"])
        self.assertLessEqual(sum(self.http.post.call_args.kwargs["timeout"]), 20)
        self.assertLessEqual(sum(self.http.send.call_args.kwargs["timeout"]), 7)

    def test_exhausted_lambda_budget_does_not_call_google(self):
        context = Mock(get_remaining_time_in_millis=lambda: 1500)
        self.assertEqual(504, self.invoke(context)["statusCode"])
        self.http.post.assert_not_called()
        self.http.send.assert_not_called()

    def test_response_closed_on_success_and_size_failure(self):
        response = Mock(status_code=200, headers=self.headers, iter_content=lambda **kwargs: iter([self.payload]))
        self.http.send.side_effect = None
        self.http.send.return_value = response
        self.invoke()
        response.close.assert_called_once()
        response.close.reset_mock()
        self.payload = b"x" * 5_600_000
        self.assertEqual(502, self.invoke()["statusCode"])
        response.close.assert_called_once()

    def test_request_body_limit_cannot_be_raised_above_four_mib(self):
        with patch.dict(os.environ, {"GOG_MAX_REQUEST_BYTES": "9500000"}):
            response = self.invoke(body="x" * (4 * 1024 * 1024 + 1))
        self.assertEqual(413, response["statusCode"])
        self.http.post.assert_not_called()
        self.http.send.assert_not_called()

    def test_original_host_is_forwarded_and_proxy_headers_are_stripped(self):
        response = self.invoke(headers={"X-GOG-Account": self.account, "x-gog-google-host": "analyticsdata.googleapis.com"}, path="/v1beta/properties/123:runReport")
        self.assertEqual(200, response["statusCode"])
        self.assertEqual("https://analyticsdata.googleapis.com/v1beta/properties/123:runReport", self.http.send.call_args.args[0].url)
        forwarded = {key.lower() for key in self.http.send.call_args.args[0].headers}
        self.assertNotIn("x-gog-google-host", forwarded)
        self.assertNotIn("x-gog-account", forwarded)

    def test_missing_original_host_fails_before_google(self):
        response = self.invoke(headers={"X-GOG-Account": self.account})
        self.assertEqual(400, response["statusCode"])
        self.http.post.assert_not_called()
        self.http.send.assert_not_called()

    def test_service_account_token_reuse_and_account_isolation_with_google_auth(self):
        # Offline integration: use real Google signing/refresh code with an
        # intercepted token endpoint, without sending credentials externally.
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.cfg = {
            "type": "service_account", "scopes": ["scope-a"],
            "service_account": {
                "type": "service_account", "client_email": "service@example.com",
                "token_uri": "https://oauth2.googleapis.com/token",
                "private_key": private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            },
        }
        token_requests = []

        def request(method, url, **kwargs):
            if url == "https://oauth2.googleapis.com/token":
                token_requests.append(kwargs)
                return Mock(status_code=200, headers={}, content=json.dumps({"access_token": f"service-{len(token_requests)}", "expires_in": 3600}).encode())
            return Mock(status_code=200, headers=self.headers, iter_content=lambda **kwargs: iter([self.payload]))

        self.http.request.side_effect = request
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(1, len(token_requests))
        self.account = "other@example.com"
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(2, len(token_requests))
        self.cfg["scopes"] = ["scope-b"]
        self.assertEqual(200, self.invoke()["statusCode"])
        self.assertEqual(3, len(token_requests))

    def test_lowercase_content_type_preserves_binary_bytes(self):
        self.headers = {"content-type": "application/octet-stream"}
        self.payload = b"\xff\x00\xfe"
        response = self.invoke()
        self.assertTrue(response["isBase64Encoded"])
        self.assertEqual(self.payload, base64.b64decode(response["body"]))

    def test_streamed_body_deadline_returns_504_and_closes_response(self):
        clock = [100.0]

        def chunks(**kwargs):
            clock[0] = 128.0
            yield b"late"

        response = Mock(status_code=200, headers=self.headers, iter_content=chunks)
        self.http.send.side_effect = None
        self.http.send.return_value = response
        with patch("time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(504, self.invoke()["statusCode"])
        response.close.assert_called_once()

    def test_prepared_request_preserves_repeated_query_values(self):
        response = self.invoke(path="/drive/v3/files", headers={"X-GOG-Account": self.account, "X-GOG-Google-Host": "www.googleapis.com"},
            multiValueQueryStringParameters={"fields": ["id", "name"], "alt": ["media"]},
            queryStringParameters={"fields": "name", "alt": "media"})
        self.assertEqual(200, response["statusCode"])
        request = self.http.send.call_args.args[0]
        self.assertEqual("https://www.googleapis.com/drive/v3/files?fields=id&fields=name&alt=media", request.url)

    def test_mirrored_gateway_headers_are_unambiguous(self):
        headers = {"X-GOG-Account": self.account, "X-GOG-Google-Host": "gmail.googleapis.com"}
        self.assertEqual(200, self.invoke(headers=headers, multiValueHeaders={key: [value] for key, value in headers.items()})["statusCode"])

    def test_policy_receives_prepared_path_not_escaped_unreserved_original(self):
        policy = policy_engine._compose_policy_documents([policy_engine._normalize_policy_document({"rules": [{"effect": "allow", "match": {"path_regex": "^/gmail/v1/users/me/labels$"}}]}, source="default", source_rank=0)])
        with patch.dict(os.environ, {"GOG_POLICY_ENABLED": "true"}), patch.object(authorization, "_load_effective_policy", return_value=policy):
            self.assertEqual(200, self.invoke(path="/gmail/v1/users/me/%6cabels")["statusCode"])
        self.assertEqual("/gmail/v1/users/me/labels", self.http.send.call_args.args[0].path_url)


    def test_policy_events_report_prepared_destination_and_outcome(self):
        for effect, expected_status in (("allow", 200), ("deny", 403)):
            with self.subTest(effect=effect):
                document = {"rules": [{"id": "matched-rule", "effect": effect,
                    "match": {"path_regex": "^/gmail/v1/users/me/labels$"},
                    "on_match": {"emit_event": True, "event_type": "audit"}}]}
                effective = policy_engine._compose_policy_documents([
                    policy_engine._normalize_policy_document(document, source="default", source_rank=0)])
                events = Mock()
                self.http.send.reset_mock()
                with patch.dict(os.environ, {"GOG_POLICY_ENABLED": "true", "GOG_POLICY_EVENTS_ENABLED": "true",
                        "GOG_POLICY_EVENT_BUS_ARN": "test-bus"}), \
                        patch.object(authorization, "_load_effective_policy", return_value=effective), \
                        patch.object(authorization, "_EVENTS", events):
                    response = self.invoke(path="/gmail/v1/users/me/%6cabels")
                self.assertEqual(expected_status, response["statusCode"])
                events.put_events.assert_called_once()
                entry = events.put_events.call_args.kwargs["Entries"][0]
                self.assertEqual("test-bus", entry["EventBusName"])
                self.assertEqual("audit", entry["DetailType"])
                detail = json.loads(entry["Detail"])
                self.assertEqual(effect, detail["decision"])
                self.assertEqual(expected_status, detail["status_code"])
                self.assertEqual("/gmail/v1/users/me/labels", detail["path"])
                self.assertEqual("matched-rule", detail["rule_id"])
                if effect == "deny":
                    self.http.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
