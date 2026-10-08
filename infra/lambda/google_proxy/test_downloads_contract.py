"""Signed-link downloads through the authorized Google proxy contract."""
import base64
import json
import os
import unittest
from unittest.mock import Mock, patch

import policy as policy_engine
import proxy_authorization as authorization
import test_handler_contract


class DownloadContractTests(unittest.TestCase):
    invoke = test_handler_contract.ProxyContractTests.invoke

    def setUp(self):
        test_handler_contract.ProxyContractTests.setUp(self)
        self.download_env = patch.dict(os.environ, {"GOG_DOWNLOAD_BUCKET": "private-downloads"})
        self.download_env.start()
        self.addCleanup(self.download_env.stop)
        self.stored = {}
        self.s3 = Mock()

        def put(**kwargs):
            self.stored.update(kwargs)
            self.stored["bytes"] = kwargs["Body"].read()

        self.s3.put_object.side_effect = put
        self.s3.generate_presigned_url.return_value = "https://private-downloads.s3.us-east-2.amazonaws.com/downloads/object?X-Amz-Signature=private"
        self.client_patch = patch("boto3.client", return_value=self.s3)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def download(self, **overrides):
        event = {
            "path": "/drive/v3/files/example",
            "queryStringParameters": {"alt": "media"},
            "headers": {"X-GOG-Account": self.account, "X-GOG-Google-Host": "www.googleapis.com",
                        "X-GOG-Response-Mode": "signed-url"},
        }
        event.update(overrides)
        return self.invoke(**event)

    def test_ten_megabyte_pdf_returns_private_link_and_original_headers(self):
        self.payload = b"%PDF-" + b"x" * 10_000_000
        self.headers = {"Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="pass.pdf"'}
        response = self.download()
        self.assertEqual(200, response["statusCode"])
        self.assertEqual("application/vnd.gog.download+json", response["headers"]["Content-Type"])
        envelope = json.loads(response["body"])
        self.assertEqual(self.payload, self.stored["bytes"])
        self.assertEqual(len(self.payload), envelope["size"])
        self.assertEqual(200, envelope["status"])
        self.assertEqual({**self.headers, "Cache-Control": "no-store"}, envelope["headers"])
        self.assertEqual(self.s3.generate_presigned_url.return_value, envelope["url"])
        self.assertEqual("private-downloads", self.stored["Bucket"])
        self.assertRegex(self.stored["Key"], r"^downloads/dl_[1-9A-HJ-NP-Za-km-z]{22}$")
        self.assertEqual(300, self.s3.generate_presigned_url.call_args.kwargs["ExpiresIn"])
        self.assertNotIn("x-gog-response-mode", {key.lower() for key in self.http.send.call_args.args[0].headers})

    def test_google_document_export_also_returns_a_link(self):
        response = self.download(path="/drive/v3/files/example/export", queryStringParameters={"mimeType": "text/plain"})
        self.assertEqual(self.payload, self.stored["bytes"])
        self.assertEqual(200, json.loads(response["body"])["status"])

    def test_without_opt_in_download_remains_an_ordinary_response(self):
        response = self.download(headers={"X-GOG-Account": self.account, "X-GOG-Google-Host": "www.googleapis.com"})
        self.assertEqual(self.payload, base64.b64decode(response["body"]))
        self.s3.put_object.assert_not_called()

    def test_metadata_and_error_responses_are_not_staged(self):
        response = self.download(queryStringParameters={"fields": "id,name"})
        self.assertEqual(self.payload, base64.b64decode(response["body"]))
        self.s3.put_object.assert_not_called()
        self.http.send.side_effect = None
        self.http.send.return_value = Mock(status_code=403, headers={"Content-Type": "application/json"},
                                         iter_content=lambda **kwargs: iter([b'{"error":"denied"}']))
        self.assertEqual(403, self.download()["statusCode"])
        self.s3.put_object.assert_not_called()

    def test_account_denial_precedes_staging(self):
        with patch.object(authorization, "_is_allowed", return_value=False):
            self.assertEqual(403, self.download()["statusCode"])
        self.http.send.assert_not_called()
        self.s3.put_object.assert_not_called()

    def test_upload_failure_does_not_expose_secrets_or_return_a_link(self):
        self.s3.put_object.side_effect = RuntimeError("secret download URL")
        response = self.download()
        self.assertEqual(502, response["statusCode"])
        self.assertNotIn("secret", response["body"])
        self.s3.generate_presigned_url.assert_not_called()
        self.s3.delete_object.assert_called_once()

    def test_presigning_failure_deletes_the_staged_object(self):
        self.s3.generate_presigned_url.side_effect = RuntimeError("secret")
        self.assertEqual(502, self.download()["statusCode"])
        self.s3.delete_object.assert_called_once()

    def test_range_response_is_not_staged(self):
        self.http.send.side_effect = None
        self.http.send.return_value = Mock(status_code=206, headers=self.headers,
                                         iter_content=lambda **kwargs: iter([self.payload]))
        self.assertEqual(206, self.download()["statusCode"])
        self.s3.put_object.assert_not_called()

    def test_staging_limit_fails_before_upload_and_closes_google_response(self):
        response = Mock(status_code=200, headers=self.headers,
                        iter_content=lambda **kwargs: iter([b"too large"]))
        self.http.send.side_effect = None
        self.http.send.return_value = response
        with patch("downloads.MAX_STAGED_BYTES", 4):
            self.assertEqual(502, self.download()["statusCode"])
        self.s3.put_object.assert_not_called()
        response.close.assert_called_once()

    def test_deadline_during_read_fails_before_upload_and_closes_response(self):
        clock = [100.0]

        def chunks(**kwargs):
            clock[0] = 128.0
            yield b"late"

        response = Mock(status_code=200, headers=self.headers, iter_content=chunks)
        self.http.send.side_effect = None
        self.http.send.return_value = response
        with patch("time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(504, self.download()["statusCode"])
        self.s3.put_object.assert_not_called()
        response.close.assert_called_once()

    def test_policy_denial_precedes_google_and_s3(self):
        policy = policy_engine._compose_policy_documents([
            policy_engine._normalize_policy_document({"rules": [
                {"effect": "deny", "match": {"path_regex": ".*"}},
            ]}, source="default", source_rank=0),
        ])
        with patch.dict(os.environ, {"GOG_POLICY_ENABLED": "true"}), \
                patch.object(authorization, "_load_effective_policy", return_value=policy):
            self.assertEqual(403, self.download()["statusCode"])
        self.http.send.assert_not_called()
        self.s3.put_object.assert_not_called()
