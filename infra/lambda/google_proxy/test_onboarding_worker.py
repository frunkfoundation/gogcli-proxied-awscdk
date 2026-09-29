import unittest
from unittest.mock import Mock, patch

import onboarding_worker as worker


class VerificationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(worker.os.environ, {"PROXY_BASE_URL": "https://example.execute-api.us-east-2.amazonaws.com/prd/"})
        patcher.start()
        self.addCleanup(patcher.stop)
    def test_empty_mailbox_verifies_via_signed_normal_proxy(self):
        http = Mock()
        http.get.side_effect = [Mock(status_code=200, json=lambda: {"emailAddress": "jay@example.com"}),
                                Mock(status_code=200, json=lambda: {"resultSizeEstimate": 0})]
        credentials = Mock()
        credentials.get_frozen_credentials.return_value = Mock(access_key="AKID", secret_key="secret", token="session")
        api = Mock()
        api.get_api_key.return_value = {"value": "server-only-key"}
        with patch.dict(worker.os.environ, {"PROXY_BASE_URL": "https://example.execute-api.us-east-2.amazonaws.com/prd/", "PROXY_API_KEY_ID": "key-id", "AWS_REGION": "us-east-2"}), patch.object(worker, "aws_client", return_value=api), patch.object(worker, "aws_credentials", return_value=credentials):
            worker.verify_proxy("jay@example.com", http)
        self.assertEqual(2, api.get_api_key.call_count)
        api.get_api_key.assert_called_with(apiKey="key-id", includeValue=True)
        self.assertEqual(2, http.get.call_count)
        first, second = http.get.call_args_list
        self.assertTrue(first.args[0].endswith("/gmail/v1/users/me/profile"))
        self.assertIn("AWS4-HMAC-SHA256", first.kwargs["headers"]["Authorization"])
        self.assertEqual("jay@example.com", first.kwargs["headers"]["X-GOG-Account"])
        from routing import resolve_google_host_and_path
        self.assertEqual(("gmail.googleapis.com", "/gmail/v1/users/me/profile"),
            resolve_google_host_and_path("/gmail/v1/users/me/profile", first.kwargs["headers"].get("X-GOG-Google-Host", "")))
        self.assertIn("x-gog-google-host", first.kwargs["headers"]["Authorization"].split("SignedHeaders=")[1].split(",")[0])
        self.assertIn("maxResults=1", second.args[0])
        self.assertIn("fields=messages%2Fid%2CresultSizeEstimate", second.args[0])

    def test_profile_must_match_expected_account(self):
        http = Mock()
        http.get.return_value = Mock(status_code=200, json=lambda: {"emailAddress": "someone@example.com"})
        with patch.object(worker, "signed_headers", return_value={}), self.assertRaises(worker.IdentityMismatch):
            worker.verify_proxy("jay@example.com", http)
        self.assertEqual(1, http.get.call_count)

    def test_permission_block_preserves_actionable_error(self):
        http = Mock()
        http.get.return_value = Mock(status_code=403)
        with patch.object(worker, "signed_headers", return_value={}), self.assertRaises(worker.PermissionBlocked):
            worker.verify_proxy("jay@example.com", http)


class WorkerStateTests(unittest.TestCase):
    def setUp(self):
        from test_onboarding_operations import MemoryStore
        self.store = MemoryStore()
        self.operation_id = "op_" + "1" * 22
        self.operation = {"pk": "OP#" + self.operation_id, "id": self.operation_id,
            "email": "jay@example.com", "phase": "verifying", "status": "running", "promoted_at": 100,
            "google_subject": "subject", "client_revision": "revision", "candidate_ref": "/test/onboarding/candidates/one"}
        self.store.items[self.operation["pk"]] = self.operation
        self.store.items["ACCOUNT#jay@example.com"] = {"active_operation": self.operation_id, "lock_expires": 2000}
        self.store.ssm = Mock()
        self.store.table = Mock()
        self.claim = patch.object(worker, "claim", side_effect=lambda store, op, now: (op, "lease"))
        self.finish = patch.object(worker, "finish", side_effect=lambda store, op, lease, changes: self.operation.update(changes))
        self.claim.start()
        self.finish.start()
        self.addCleanup(self.claim.stop)
        self.addCleanup(self.finish.stop)

    def test_cache_wait_reschedules_without_reading_proxy(self):
        with patch.object(worker, "enqueue") as enqueue, patch.object(worker, "verify_proxy") as verify:
            worker.process(self.operation_id, self.store, now=164)
        enqueue.assert_called_once_with(self.operation_id, 1)
        verify.assert_not_called()
        self.assertEqual("verifying", self.operation["phase"])

    def test_permission_block_keeps_saved_credentials_for_retry(self):
        with patch.object(worker, "verify_proxy", side_effect=worker.PermissionBlocked):
            worker.process(self.operation_id, self.store, now=165)
        self.assertEqual("verification_failed", self.operation["phase"])
        self.assertEqual("permission_blocked", self.operation["error_code"])
        self.store.ssm.put_parameter.assert_not_called()
        self.assertIn("retained", self.operation["recovery"])
        self.assertNotIn("active_operation", self.store.items["ACCOUNT#jay@example.com"])

    def test_transient_failures_are_bounded(self):
        with patch.object(worker, "verify_proxy", side_effect=worker.TransientFailure):
            worker.process(self.operation_id, self.store, now=165)
            self.assertEqual("verifying", self.operation["phase"])
            self.assertEqual(225, self.operation["next_attempt"])
            self.operation["attempts"] = 4
            worker.process(self.operation_id, self.store, now=300)
        self.assertEqual("verification_failed", self.operation["phase"])
        self.assertEqual("retry_exhausted", self.operation["error_code"])

    def test_promotion_is_recoverable_and_waits_before_normal_reads(self):
        self.operation["phase"] = "pending_promotion"
        self.operation.pop("promoted_at")
        self.store.secrets[self.operation["candidate_ref"]] = {"type": "oauth_refresh", "client_id": "client", "client_secret": "secret", "refresh_token": "refresh"}
        with patch.object(worker, "extend_allowlists"), patch.object(worker, "verify_proxy") as verify:
            worker.process(self.operation_id, self.store, now=100)
        written = self.store.ssm.put_parameter.call_args.kwargs
        self.assertEqual("SecureString", written["Type"])
        self.assertEqual("alias/aws/ssm", written["KeyId"])
        self.assertEqual("verifying", self.operation["phase"])
        self.assertEqual(100, self.operation["promoted_at"])
        self.assertEqual(165, self.operation["next_attempt"])
        verify.assert_not_called()

    def test_stale_operation_cannot_replace_newer_account(self):
        self.operation["phase"] = "pending_promotion"
        self.operation.pop("promoted_at")
        self.store.items["ACCOUNT#jay@example.com"]["active_operation"] = "newer"
        worker.process(self.operation_id, self.store, now=100)
        self.store.ssm.put_parameter.assert_not_called()
        self.assertEqual("identity_mismatch", self.operation["error_code"])

    def test_ttl_cleanup_deletes_only_temporary_secrets(self):
        installed = "/test/google/accounts/mailbox"
        temporary = "/test/onboarding/candidates/one"
        self.store.secrets.update({installed: {}, temporary: {}})
        worker.cleanup(self.store, {"candidate_ref": temporary, "client_ref": installed, "attempt_ref": installed})
        self.assertNotIn(temporary, self.store.secrets)
        self.assertIn(installed, self.store.secrets)

    def test_interrupted_exchange_waits_until_callback_deadline(self):
        self.operation.update(phase="exchanging", promoted_at=0, exchange_deadline_at=220)
        with patch.object(worker, "enqueue") as enqueue:
            worker.process(self.operation_id, self.store, now=200)
        enqueue.assert_called_once_with(self.operation_id, 20)
        self.assertEqual("exchanging", self.operation["phase"])

    def test_saved_exchange_response_recovers_without_exchanging_code_again(self):
        callback_ref = "/test/onboarding/callbacks/one"
        self.operation.update(phase="exchanging", promoted_at=0, exchange_deadline_at=120, callback_ref=callback_ref)
        self.store.secrets[callback_ref] = {"tokens": {"refresh_token": "saved"}, "nonce": "saved"}
        with patch("onboarding_google.GoogleClient.exchange") as exchange:
            worker.process(self.operation_id, self.store, now=200)
        self.assertEqual("validating", self.operation["phase"])
        self.assertTrue(self.operation["exchange_saved"])
        exchange.assert_not_called()
        self.assertIn(callback_ref, self.store.secrets)

    def test_interrupted_unsaved_exchange_requires_fresh_consent(self):
        from botocore.exceptions import ClientError
        self.operation.update(phase="exchanging", promoted_at=0, exchange_deadline_at=120, callback_ref="/test/onboarding/callbacks/one")
        self.store.secret_get = Mock(side_effect=ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter"))
        with patch("onboarding_google.GoogleClient.exchange") as exchange:
            worker.process(self.operation_id, self.store, now=200)
        self.assertEqual("failed", self.operation["phase"])
        self.assertEqual("consent_exchange_interrupted", self.operation["error_code"])
        exchange.assert_not_called()

    def test_validation_transient_failure_retains_saved_google_response(self):
        self.operation.update(phase="validating", promoted_at=0, callback_ref="/test/onboarding/callbacks/one", exchange_saved=True)
        self.store.secrets[self.operation["callback_ref"]] = {"tokens": {"refresh_token": "saved"}, "nonce": "saved"}
        with patch.dict(worker.os.environ, {"GOOGLE_CALLBACK_URL": "https://app.test/callback"}), patch("onboarding_operations.OnboardingService.validate_candidate_operation", side_effect=worker.WorkflowError(400, "candidate_unusable", "unusable")):
            worker.process(self.operation_id, self.store, now=200)
        self.assertEqual("validating", self.operation["phase"])
        self.assertIn(self.operation["callback_ref"], self.store.secrets)
        self.assertEqual(260, self.operation["next_attempt"])


class DeliveryTests(unittest.TestCase):
    def image(self, value):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        return {key: serializer.serialize(item) for key, item in value.items()}

    def test_outbox_dispatches_phase_and_delay_but_not_lease_updates(self):
        pending = {"pk": "OP#op_" + "1" * 22, "id": "op_" + "1" * 22, "phase": "pending_promotion"}
        record = {"eventSource": "aws:dynamodb", "eventName": "MODIFY", "eventID": "1",
            "dynamodb": {"NewImage": self.image(pending), "OldImage": self.image({**pending, "phase": "exchanging"})}}
        with patch.object(worker, "enqueue") as enqueue:
            self.assertEqual([], worker.handler({"Records": [record]}, None)["batchItemFailures"])
            enqueue.assert_called_once()
            enqueue.reset_mock()
            record["dynamodb"]["OldImage"] = self.image(pending)
            record["dynamodb"]["NewImage"] = self.image({**pending, "lease_until": 120})
            worker.handler({"Records": [record]}, None)
            enqueue.assert_not_called()

    def test_poison_operation_records_actionable_failed_status_from_dlq(self):
        from test_onboarding_operations import MemoryStore
        store = MemoryStore()
        operation_id = "op_" + "1" * 22
        store.items["OP#" + operation_id] = {"pk": "OP#" + operation_id, "id": operation_id,
            "email": "jay@example.com", "phase": "verifying", "status": "running"}
        store.items["ACCOUNT#jay@example.com"] = {"active_operation": operation_id, "lock_expires": 2000}
        event = {"Records": [{"eventSource": "aws:sqs", "eventSourceARN": "dlq", "messageId": "1", "body": operation_id}]}
        with patch.dict(worker.os.environ, {"ONBOARDING_DLQ_ARN": "dlq"}), patch.object(worker, "runtime_store", return_value=store):
            self.assertEqual([], worker.handler(event, None)["batchItemFailures"])
        self.assertEqual("verification_failed", store.items["OP#" + operation_id]["phase"])
        self.assertEqual("worker_delivery_failed", store.items["OP#" + operation_id]["error_code"])
        self.assertNotIn("active_operation", store.items["ACCOUNT#jay@example.com"])

    def test_durable_delivery_failure_retries_stream_record(self):
        event = {"Records": [{"eventSource": "aws:dynamodb", "eventName": "INSERT", "eventID": "stream-id",
            "dynamodb": {"NewImage": self.image({"pk": "OP#one", "id": "op_one", "phase": "pending_promotion"})}}]}
        with patch.object(worker, "enqueue", side_effect=RuntimeError):
            self.assertEqual({"batchItemFailures": [{"itemIdentifier": "stream-id"}]}, worker.handler(event, None))


class AllowlistTests(unittest.TestCase):
    def test_only_explicit_emails_are_retained_for_both_callers(self):
        store = Mock(prefix="/test")
        store.secret_get.return_value = {"emails": ["first@example.com", "*"], "domains": ["example.com", "*"]}
        with patch.dict(worker.os.environ, {"ONBOARDING_VERIFIER_ROLE_ARN": "arn:aws:iam::968138089939:role/verifier"}):
            worker.extend_allowlists(store, "second@example.com")
        self.assertEqual(2, store.ssm.put_parameter.call_count)
        for call in store.ssm.put_parameter.call_args_list:
            import json
            self.assertEqual({"emails": ["first@example.com", "second@example.com"]}, json.loads(call.kwargs["Value"]))
        self.assertEqual(4, store.table.update_item.call_count)

    def test_cleanup_owner_login_secret_preserves_client_secret(self):
        store = Mock(prefix="/test")
        worker.cleanup(store, {"secret_ref": "/test/onboarding/clients/current"})
        store.secret_delete.assert_not_called()
        worker.cleanup(store, {"secret_ref": "/test/onboarding/owner-logins/expired"})
        store.secret_delete.assert_called_once_with("/test/onboarding/owner-logins/expired")


if __name__ == "__main__":
    unittest.main()
