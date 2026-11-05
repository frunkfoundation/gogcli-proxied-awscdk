"""Durable effects: withdrawn access wins, interrupted writes recover, probes stay read-only."""
import copy
import os
import unittest
from unittest.mock import Mock, patch

import onboarding_worker as worker
from onboarding_effects import (allow_parameter, apply_access, policy_parameter, promote, remove_connection,
                                set_allowlist, validate_allowlist)
from onboarding_services import content_hash, requested_scopes, service_policy
from onboarding_store import WorkflowError, account_parameter
import test_onboarding_lifecycle as lifecycle


class WorkerLifecycleTests(unittest.TestCase):
    enroll = lifecycle.AccountLifecycleTests.enroll
    # Reuse account preparation only; the inherited public-command tests remain valid.
    def setUp(self):
        lifecycle.AccountLifecycleTests.setUp(self)
        self.env_worker = patch.dict(os.environ, {"GOOGLE_CALLBACK_URL": self.service.callback_url,
            "PROXY_BASE_URL": "https://api.example/prd/", "PROXY_API_KEY_ID": "key"})
        self.env_worker.start()
        self.addCleanup(self.env_worker.stop)

    def candidate(self):
        response = self.enroll()
        op = self.store.get("OP#" + response["id"])
        op.update(phase="pending_promotion", status="running", services=["gmail", "drive"], next_attempt=100,
            google_subject="subject", granted_scopes=requested_scopes(["gmail", "drive"]), credential_revision=op["id"])
        op["candidate_ref"] = self.store.secret_put("candidates/" + op["id"], {"type": "oauth_refresh",
            "client_id": "client", "client_secret": "secret", "refresh_token": "token", "scopes": op["granted_scopes"],
            "credential_revision": op["id"]})
        self.store.put(op)
        return op

    def test_promotion_retains_owner_access_off_and_waits_from_final_write(self):
        account = self.store.get("ACCOUNT#jay@example.com")
        account["desired_owner_access"] = False
        self.store.put(account)
        op = self.candidate()
        with patch("onboarding_effects.time.time", return_value=140):
            result = promote(self.store, op, 100)
        self.assertEqual(205, result["next_attempt"])
        self.assertEqual(140, result["authorization_written_at"])
        self.assertNotIn(op["email"], self.store.secret_get(allow_parameter(self.store, True))["emails"])
        self.assertIn(op["email"], self.store.secret_get(allow_parameter(self.store, False))["emails"])
        self.assertEqual("connected", self.store.get("ACCOUNT#jay@example.com")["connection_status"])

    def test_disconnect_racing_promotion_cannot_be_overwritten(self):
        op = self.candidate()
        original_put = self.store.parameter_put
        accepted = []
        def put_and_disconnect(name, value):
            original_put(name, value)
            if name == account_parameter(self.store.prefix, op["email"]):
                accepted.append(self.service.disconnect("owner", op["email"]))
        self.store.parameter_put = put_and_disconnect
        with self.assertRaises(WorkflowError):
            promote(self.store, op, 100)
        self.store.parameter_put = original_put
        disconnected = self.store.get("OP#" + accepted[0]["id"])
        with patch("onboarding_effects.time.time", return_value=160):
            changes = remove_connection(self.store, disconnected, 140)
        self.store.transition(disconnected["pk"], "removing", changes)
        worker.process(disconnected["id"], self.store, now=225)
        self.assertEqual("disconnected", self.store.get("ACCOUNT#jay@example.com")["connection_status"])
        self.assertNotIn(account_parameter(self.store.prefix, op["email"]), self.store.secrets)
        worker.process(op["id"], self.store, now=230)
        self.assertNotIn(account_parameter(self.store.prefix, op["email"]), self.store.secrets)

    def test_partial_policy_write_is_recoverable_without_overwriting_operator_edits(self):
        op = self.candidate()
        original_put = self.store.parameter_put
        def fail_credential(name, value):
            if name == account_parameter(self.store.prefix, op["email"]):
                raise RuntimeError("transient store outage")
            original_put(name, value)
        self.store.parameter_put = fail_credential
        with self.assertRaises(RuntimeError):
            promote(self.store, op, 100)
        updated = self.store.get(op["pk"])
        self.assertEqual(content_hash(service_policy(op["services"])), updated["policy_write_hash"])
        worker.preserve_recovery(self.store, updated, {"phase": "failed"}, int(__import__("time").time()))
        self.store.terminal(updated, {"phase": "failed", "status": "failed"}, int(__import__("time").time()))
        recovered = self.service.recover("owner", op["email"], "recovery")
        new = self.store.get("OP#" + recovered["id"])
        self.assertEqual(updated["policy_write_hash"], new["policy_write_hash"])
        self.assertNotEqual(updated["candidate_ref"], new["candidate_ref"])
        self.store.parameter_put = original_put
        new.update(phase="pending_promotion", validation_complete=True)
        self.store.put(new)
        with patch("onboarding_effects.time.time", return_value=150):
            promote(self.store, new, 140)
        self.assertEqual(new["id"], self.store.get("ACCOUNT#jay@example.com")["credential_revision"])

    def test_operator_customized_policy_is_not_replaced(self):
        op = self.candidate()
        tightened = {"version": 1, "default_effect": "deny", "rules": []}
        self.store.parameter_put(policy_parameter(self.store, op["email"]), tightened)
        with self.assertRaises(WorkflowError) as failed:
            promote(self.store, op, 100)
        self.assertEqual("operator_policy_requires_review", failed.exception.code)
        self.assertEqual(tightened, self.store.secret_get(policy_parameter(self.store, op["email"])))
        self.assertNotIn(account_parameter(self.store.prefix, op["email"]), self.store.secrets)

    def test_access_off_during_disconnect_does_not_cancel_removal(self):
        self.enroll()
        removal = self.service.disconnect("owner", "jay@example.com")
        self.assertEqual(removal["id"], self.service.access("owner", "jay@example.com", False)["id"])
        self.assertEqual("removing", self.store.get("OP#" + removal["id"])["phase"])

    def test_whitespace_wildcard_or_domain_grant_requires_operator(self):
        for config in ({"emails": [" * "]}, {"emails": [], "domains": ["other.example"]}):
            self.store.parameter_put(allow_parameter(self.store, True), config)
            with self.subTest(config=config), self.assertRaises(WorkflowError) as failed:
                self.service.access("owner", "jay@example.com", False)
            self.assertEqual("broad_allowlist_requires_operator", failed.exception.code)
            self.assertEqual(config, self.store.secret_get(allow_parameter(self.store, True)))

    def test_promotion_wait_avoids_proxy_until_cache_window_passes(self):
        op = self.candidate()
        with patch("onboarding_effects.time.time", return_value=140):
            worker.process(op["id"], self.store, now=100)
        with patch.object(worker, "enqueue") as enqueue, patch.object(worker, "verify_proxy") as verify:
            worker.process(op["id"], self.store, now=204)
        verify.assert_not_called()
        enqueue.assert_called_once_with(op["id"], 1)
        with patch.object(worker, "verify_proxy", return_value={"gmail": {"status": "succeeded"}, "drive": {"status": "failed", "error_code": "permission_blocked"}}):
            worker.process(op["id"], self.store, now=205)
        account = self.store.get("ACCOUNT#jay@example.com")
        self.assertEqual("connected", account["connection_status"])
        self.assertEqual("succeeded", account["verifications"]["gmail"]["status"])
        self.assertEqual("failed", account["verifications"]["drive"]["status"])
        self.assertNotIn("active_operation", account)

    def test_new_key_cannot_alias_an_unexpired_consent_attempt(self):
        op = self.enroll()
        with self.assertRaises(WorkflowError) as failed:
            self.enroll(key="different")
        self.assertEqual("operation_conflict", failed.exception.code)
        self.assertEqual(op["id"], self.enroll()["id"])

    def test_consent_expiry_is_terminal_without_ttl_removing_installed_state(self):
        result = self.enroll()
        op = self.store.get("OP#" + result["id"])
        self.assertNotIn("expires_at", op)
        worker.process(op["id"], self.store, now=op["consent_expires_at"])
        self.assertEqual("failed", self.store.get(op["pk"])["status"])
        self.assertNotIn("active_operation", self.store.get("ACCOUNT#jay@example.com"))
        self.assertEqual("not_connected", self.store.get("ACCOUNT#jay@example.com")["connection_status"])


class ProxyProbeTests(unittest.TestCase):
    def test_drive_only_signed_probe_checks_identity_and_one_id(self):
        http = Mock()
        http.get.side_effect = [Mock(status_code=200, json=lambda: {"user": {"emailAddress": "jay@example.com"}}),
                                Mock(status_code=200, json=lambda: {"files": []})]
        with patch.dict(os.environ, {"PROXY_BASE_URL": "https://api.example/prd/", "PROXY_API_KEY_ID": "key"}), \
             patch.object(worker, "aws_client") as api, patch.object(worker, "signed_headers", return_value={}) as signed:
            api.return_value.get_api_key.return_value = {"value": "private-key"}
            result = worker.verify_proxy("jay@example.com", http, ["drive"])
        self.assertEqual({"drive": {"status": "succeeded"}}, result)
        self.assertEqual(2, http.get.call_count)
        self.assertIn("pageSize=1", http.get.call_args_list[1].args[0])
        self.assertEqual("www.googleapis.com", signed.call_args.args[2])
        self.assertTrue(all("drive/v3" in call.args[0] for call in http.get.call_args_list))

    def test_failed_gmail_check_does_not_prevent_separate_drive_result(self):
        http = Mock()
        http.get.side_effect = [Mock(status_code=403, json=lambda: {"error": "policy_denied"}),
            Mock(status_code=200, json=lambda: {"user": {"emailAddress": "jay@example.com"}}),
            Mock(status_code=200, json=lambda: {"files": []})]
        with patch.dict(os.environ, {"PROXY_BASE_URL": "https://api.example/prd/", "PROXY_API_KEY_ID": "key"}), \
             patch.object(worker, "aws_client") as api, patch.object(worker, "signed_headers", return_value={}):
            api.return_value.get_api_key.return_value = {"value": "private-key"}
            result = worker.verify_proxy("jay@example.com", http, ["gmail", "drive"])
        self.assertEqual("failed", result["gmail"]["status"])
        self.assertEqual("succeeded", result["drive"]["status"])


class DeliveryTests(unittest.TestCase):
    def image(self, value):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        return {key: serializer.serialize(item) for key, item in value.items()}

    def test_stream_outbox_dispatches_phase_changes_but_not_lease_changes(self):
        pending = {"pk": "OP#op_" + "1" * 22, "id": "op_" + "1" * 22, "phase": "pending_promotion"}
        record = {"eventSource": "aws:dynamodb", "eventName": "MODIFY", "eventID": "id", "dynamodb": {
            "NewImage": self.image(pending), "OldImage": self.image({**pending, "phase": "validating"})}}
        with patch.object(worker, "enqueue") as enqueue:
            self.assertEqual([], worker.handler({"Records": [record]}, None)["batchItemFailures"])
            enqueue.assert_called_once()
            record["dynamodb"]["OldImage"] = self.image(pending)
            record["dynamodb"]["NewImage"] = self.image({**pending, "lease_until": 120})
            enqueue.reset_mock()
            worker.handler({"Records": [record]}, None)
            enqueue.assert_not_called()

    def test_cleanup_never_deletes_installed_or_client_secret(self):
        store = Mock(prefix="/test")
        for ref in ("/test/onboarding/clients/current", "/test/google/accounts/installed"):
            worker.cleanup(store, {"secret_ref": ref})
        store.secret_delete.assert_not_called()
        worker.cleanup(store, {"secret_ref": "/test/onboarding/owner-logins/expired"})
        store.secret_delete.assert_called_once_with("/test/onboarding/owner-logins/expired")
