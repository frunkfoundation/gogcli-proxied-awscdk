"""Consent callback, recovery, and removal failures through durable account actions."""
import time
import unittest
from unittest.mock import Mock, patch

import onboarding_worker as worker
from onboarding_effects import allow_parameter, policy_parameter, remove_connection
from onboarding_services import requested_scopes
from onboarding_store import WorkflowError, account_parameter
import test_onboarding_lifecycle as lifecycle


class RecoveryContracts(unittest.TestCase):
    setUp = lifecycle.AccountLifecycleTests.setUp
    enroll = lifecycle.AccountLifecycleTests.enroll

    def test_callback_is_once_and_each_service_selection_has_its_own_probes(self):
        for services in (["gmail"], ["drive"], ["gmail", "drive"]):
            with self.subTest(services=services):
                email = services[0] + str(len(services)) + "@example.com"
                self.service.save_account("owner", email, {"services": services})
                response = self.service.enroll("owner", email, "start", "session", {"services": services})
                op = self.store.get("OP#" + response["id"])
                state = self.store.secret_get(op["attempt_ref"])["state"]
                self.google.exchange.return_value = {"refresh_token": "saved-refresh", "id_token": "saved-id",
                    "scope": " ".join(requested_scopes(services))}
                result = self.service.callback(state, "code", "session")
                self.assertEqual("validating", result["phase"])
                with self.assertRaises(WorkflowError):
                    self.service.callback(state, "same-code", "session")
                self.assertEqual(1, self.google.exchange.call_count)
                self.google.exchange.reset_mock()
                claims = {"subject": "subject", "granted_scopes": requested_scopes(services)}
                current = self.store.get(op["pk"])
                with patch("onboarding_google.GoogleClient.identity", return_value=claims), \
                     patch.dict(worker.os.environ, {"GOOGLE_CALLBACK_URL": self.service.callback_url}):
                    worker.process(op["id"], self.store, now=current["next_attempt"])
                checked = self.store.get(op["pk"])
                self.assertEqual("checking_candidate", checked["phase"])
                http = Mock()
                http.post.return_value = Mock(status_code=200, json=lambda: {"access_token": "fresh"})
                responses = []
                for name in services:
                    identity = {"emailAddress": email} if name == "gmail" else {"user": {"emailAddress": email}}
                    listing = {"messages": []} if name == "gmail" else {"files": []}
                    responses.extend([Mock(status_code=200, json=lambda value=identity: value),
                                      Mock(status_code=200, json=lambda value=listing: value)])
                http.get.side_effect = responses
                worker.process(op["id"], self.store, http, now=checked["next_attempt"])
                self.assertEqual("pending_promotion", self.store.get(op["pk"])["phase"])
                self.assertEqual(2 * len(services), http.get.call_count)
                if services == ["drive"]:
                    self.assertTrue(all("drive/v3" in call.args[0] for call in http.get.call_args_list))

    def test_declined_callback_preserves_installed_connection_and_has_no_reusable_artifact(self):
        account = self.store.get("ACCOUNT#jay@example.com")
        account.update(connection_status="connected", credential_revision="installed", installed_services=["gmail"],
            granted_scopes=requested_scopes(["gmail"]))
        self.store.put(account)
        response = self.enroll()
        op = self.store.get("OP#" + response["id"])
        state = self.store.secret_get(op["attempt_ref"])["state"]
        with self.assertRaises(WorkflowError) as failed:
            self.service.callback(state, "", "session", error="access_denied")
        self.assertEqual("consent_denied", failed.exception.code)
        self.google.exchange.assert_not_called()
        account = self.store.get("ACCOUNT#jay@example.com")
        self.assertEqual("installed", account["credential_revision"])
        self.assertEqual("connected", account["connection_status"])
        with self.assertRaises(WorkflowError) as unavailable:
            self.service.recover("owner", "jay@example.com", "recover")
        self.assertEqual("consent_required", unavailable.exception.code)

    def test_unsaved_ambiguous_exchange_is_not_reexchanged_by_worker(self):
        response = self.enroll()
        op = self.store.get("OP#" + response["id"])
        self.store.transition(op["pk"], "authorizing", {"phase": "exchanging", "next_attempt": 100})
        with patch("onboarding_google.GoogleClient.exchange") as exchange:
            worker.process(op["id"], self.store, now=100)
        exchange.assert_not_called()
        self.assertEqual("consent_exchange_interrupted", self.store.get(op["pk"])["error_code"])

    def test_saved_exchange_recovers_if_callback_crashed_before_metadata_transition(self):
        response = self.enroll()
        op = self.store.get("OP#" + response["id"])
        self.store.transition(op["pk"], "authorizing", {"phase": "exchanging", "next_attempt": 100})
        self.store.secret_put("callbacks/" + op["id"], {"tokens": {"refresh_token": "saved"}, "nonce": "saved"})
        with patch("onboarding_google.GoogleClient.exchange") as exchange:
            worker.process(op["id"], self.store, now=100)
        exchange.assert_not_called()
        self.assertEqual("validating", self.store.get(op["pk"])["phase"])

    def test_saved_candidate_recovery_survives_old_operation_and_artifact_cleanup(self):
        response = self.enroll()
        op = self.store.get("OP#" + response["id"])
        ref = self.store.secret_put("candidates/" + op["id"], {"type": "oauth_refresh", "client_id": "client",
            "client_secret": "secret", "refresh_token": "refresh", "scopes": requested_scopes(["drive"]), "credential_revision": op["id"]})
        op.update(phase="checking_candidate", candidate_ref=ref, google_subject="subject", services=["drive"],
            granted_scopes=requested_scopes(["drive"]))
        self.store.put(op)
        worker.preserve_recovery(self.store, op, {"phase": "failed"}, int(time.time()))
        self.store.terminal(op, {"phase": "failed", "status": "failed"}, int(time.time()))
        self.store.items.pop(op["pk"])
        recovered = self.service.recover("owner", op["email"], "recover")
        fresh = self.store.get("OP#" + recovered["id"])
        worker.cleanup(self.store, op)
        self.assertNotIn(ref, self.store.secrets)
        self.assertIn(fresh["candidate_ref"], self.store.secrets)
        self.assertNotEqual(op["id"], fresh["id"])
        self.assertEqual(fresh["id"], self.store.secret_get(fresh["candidate_ref"])["credential_revision"])

    def test_partial_disconnect_failure_remains_retryable_without_false_success(self):
        account = self.store.get("ACCOUNT#jay@example.com")
        account.update(connection_status="connected", credential_revision="installed", installed_services=["gmail"])
        self.store.put(account)
        parameter = account_parameter(self.store.prefix, account["email"])
        self.store.parameter_put(parameter, {"refresh_token": "saved"})
        response = self.service.disconnect("owner", account["email"])
        op = self.store.get("OP#" + response["id"])
        original_put = self.store.parameter_put
        self.store.parameter_put = Mock(side_effect=RuntimeError("store unavailable"))
        worker.process(op["id"], self.store, now=op["next_attempt"])
        failed = self.store.get(op["pk"])
        self.assertEqual("removing", failed["phase"])
        self.assertEqual("running", failed["status"])
        self.assertNotIn(parameter, self.store.secrets)
        self.assertEqual("disconnecting", self.store.get(account["pk"])["connection_status"])
        self.store.parameter_put = original_put
        final_write = failed["next_attempt"] + 20
        with patch("onboarding_effects.time.time", return_value=final_write):
            worker.process(op["id"], self.store, now=failed["next_attempt"])
        pending = self.store.get(op["pk"])
        self.assertEqual("propagating", pending["phase"])
        self.assertEqual(final_write + 65, pending["next_attempt"])
        worker.process(op["id"], self.store, now=pending["next_attempt"])
        self.assertEqual("disconnected", self.store.get(account["pk"])["connection_status"])
        self.assertEqual("succeeded", self.store.get(op["pk"])["status"])

    def test_reload_exposes_original_continuation_without_oauth_secrets(self):
        response = self.enroll(services=["drive"])
        setup = self.service.setup("owner", "session")
        summary = next(item["latest_operation"] for item in setup["accounts"] if item["email"] == "jay@example.com")
        self.assertTrue(summary["consent_session_matches"])
        self.assertEqual("first", summary["continuation_key"])
        self.assertEqual({"services": ["drive"]}, summary["continuation_request"])
        self.assertNotIn("authorizationUrl", summary)
        continued = self.service.enroll("owner", "jay@example.com", summary["continuation_key"], "session", summary["continuation_request"])
        self.assertEqual(response["id"], continued["id"])
        setup = self.service.setup("owner", "different-session")
        summary = next(item["latest_operation"] for item in setup["accounts"] if item["email"] == "jay@example.com")
        self.assertFalse(summary["consent_session_matches"])

    def test_omitted_or_expired_key_has_explicit_recovery_and_no_new_operation(self):
        response = self.enroll()
        op = self.store.get("OP#" + response["id"])
        key = self.store.get(op["idempotency_pk"])
        key["expires_at"] = 1
        self.store.put(key)
        with self.assertRaises(WorkflowError) as expired:
            self.enroll()
        self.assertEqual("idempotency_expired", expired.exception.code)
        self.assertEqual(1, len(self.store.rows("OP#")))

    def test_concurrent_same_key_returns_accepted_operation_without_second_consent(self):
        accept = self.store.accept
        def concurrent(account, operation, idem, changes, supersede=False):
            accept(account, operation, idem, changes, supersede)
            raise WorkflowError(409, "operation_conflict", "Another identical command accepted first.")
        self.store.accept = concurrent
        response = self.enroll()
        self.assertEqual("preparing_consent", response["phase"])
        self.assertEqual(response["id"], self.store.get("ACCOUNT#jay@example.com")["active_operation"])
        self.google.authorization_url.assert_not_called()
        self.assertEqual(1, len(self.store.rows("OP#")))
