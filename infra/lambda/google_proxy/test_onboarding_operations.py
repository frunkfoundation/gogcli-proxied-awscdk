import copy
import unittest
from unittest.mock import Mock, patch

from onboarding_google import GoogleClient, SCOPES, validate_web_client
from onboarding_operations import OnboardingService
from onboarding_store import Store, WorkflowError, new_id


class MemoryStore:
    def __init__(self):
        self.items, self.secrets, self.prefix = {}, {}, "/test"

    def get(self, key):
        return copy.deepcopy(self.items.get(key))

    def put(self, item, absent=False):
        if absent and item["pk"] in self.items:
            raise WorkflowError(409, "conflict", "conflict")
        self.items[item["pk"]] = copy.deepcopy(item)

    def transition(self, key, expected, changes, conditions=None):
        item = self.items[key]
        if item["phase"] != expected or any(item.get(k) != v for k, v in (conditions or {}).items()):
            raise WorkflowError(409, "conflict", "conflict")
        item.update(changes)
        return copy.deepcopy(item)

    def lock_account(self, email, operation, now):
        item = self.items["ACCOUNT#" + email]
        if item.get("active_operation") and item["lock_expires"] >= now:
            raise WorkflowError(409, "conflict", "conflict")
        item.update(active_operation=operation, operation_id=operation, lock_expires=now + 1800)

    def release_account(self, email, operation):
        item = self.items["ACCOUNT#" + email]
        if item.get("active_operation") == operation:
            op = self.items.get("OP#" + operation, {})
            if op.get("phase") in {"ready", "verification_failed", "failed"}:
                item.update({key: op[key] for key in ("phase", "status", "verified_at", "promoted_at", "error", "error_code", "recovery") if key in op})
                item["terminal_operation_id"] = operation
            item.pop("active_operation")
            item.pop("lock_expires")

    def accounts(self):
        return [copy.deepcopy(value) for key, value in self.items.items() if key.startswith("ACCOUNT#")]

    def secret_put(self, name, value):
        self.secrets[name] = copy.deepcopy(value)
        return name

    def secret_get(self, name):
        return copy.deepcopy(self.secrets[name])

    def secret_delete(self, name):
        self.secrets.pop(name, None)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.google = Mock()
        self.google.authorization_url.return_value = "https://google.test/consent"
        self.google.exchange.return_value = {"refresh_token": "secret"}
        self.google.identity.return_value = "subject"
        self.service = OnboardingService(self.store, self.google, "https://app.test/callback")
        self.service.save_client("owner", {"web": {"client_id": "client", "client_secret": "secret",
            "project_id": "google-project", "redirect_uris": ["https://app.test/callback"]}})

    def enroll(self):
        result = self.service.enroll("owner", "jay@example.com", "request", "session")
        operation = self.store.get("OP#" + result["id"])
        state = self.google.authorization_url.call_args.args[2]
        return operation, state

    def test_idempotency_and_concurrent_reconnect_preserve_single_operation(self):
        operation, _ = self.enroll()
        self.assertEqual(operation["id"], self.service.enroll("owner", "jay@example.com", "request", "session")["id"])
        with self.assertRaises(WorkflowError):
            self.service.enroll("owner", "jay@example.com", "another", "session")
        with self.assertRaises(WorkflowError):
            self.service.enroll("owner", "other@example.com", "request", "session")

    def test_candidate_validates_before_durable_promotion_and_callback_is_once(self):
        operation, state = self.enroll()
        result = self.service.callback(state, "code", "session")
        self.assertEqual("validating", result["phase"])
        saved = self.store.get(operation["pk"])
        changes = self.service.validate_candidate_operation(saved)
        self.assertEqual("pending_promotion", changes["phase"])
        self.google.validate_candidate.assert_called_once()
        self.assertNotIn("refresh_token", str(self.store.items))
        self.assertIn(operation["attempt_ref"], self.store.secrets)
        with self.assertRaises(WorkflowError):
            self.service.callback(state, "code", "session")
        self.google.exchange.assert_called_once()

    def test_mismatch_leaves_existing_credentials_and_cleans_attempt(self):
        operation, state = self.enroll()
        self.store.secrets["installed"] = {"refresh_token": "old"}
        self.google.identity.side_effect = WorkflowError(400, "identity_mismatch", "bad")
        self.service.callback(state, "code", "session")
        with self.assertRaises(WorkflowError):
            self.service.validate_candidate_operation(self.store.get(operation["pk"]))
        self.assertEqual({"refresh_token": "old"}, self.store.secrets["installed"])
        self.google.validate_candidate.assert_not_called()
        self.assertEqual("validating", self.store.get(operation["pk"])["phase"])
        self.assertIn(operation["attempt_ref"], self.store.secrets)

    def test_expired_and_wrong_session_do_not_exchange(self):
        operation, state = self.enroll()
        with self.assertRaises(WorkflowError):
            self.service.callback(state, "code", "wrong")
        with patch("onboarding_operations.time.time", return_value=operation["consent_expires_at"]):
            with self.assertRaises(WorkflowError):
                self.service.callback(state, "code", "session")
        self.google.exchange.assert_not_called()

    def test_verification_retry_does_not_repeat_consent(self):
        operation, state = self.enroll()
        self.service.callback(state, "code", "session")
        self.store.transition(operation["pk"], "validating", {"phase": "verification_failed", "status": "failed", "promoted_at": 123, "validation_complete": True})
        self.store.release_account(operation["email"], operation["id"])
        result = self.service.retry("owner", operation["id"])
        self.assertEqual("verifying", result["phase"])
        self.google.exchange.assert_called_once()

    def test_exchange_saved_before_validation_and_recovered_without_reexchange(self):
        operation, state = self.enroll()
        result = self.service.callback(state, "code", "session")
        self.assertEqual("validating", result["phase"])
        self.google.identity.assert_not_called()
        self.google.validate_candidate.assert_not_called()
        saved = self.store.get(operation["pk"])
        self.assertIn(saved["callback_ref"], self.store.secrets)
        self.google.validate_candidate.side_effect = RuntimeError("network interrupted")
        with self.assertRaises(RuntimeError):
            self.service.validate_candidate_operation(saved)
        self.assertIn(saved["callback_ref"], self.store.secrets)
        self.google.validate_candidate.side_effect = None
        changes = self.service.validate_candidate_operation(saved)
        self.assertEqual("pending_promotion", changes["phase"])
        self.google.exchange.assert_called_once()
        self.google.identity.assert_called_once()

    def test_unexpected_exchange_failure_is_factual_failed_state(self):
        operation, state = self.enroll()
        self.google.exchange.side_effect = RuntimeError("network interrupted")
        with self.assertRaises(WorkflowError):
            self.service.callback(state, "code", "session")
        saved = self.store.get(operation["pk"])
        self.assertEqual("failed", saved["phase"])
        self.assertEqual("consent_exchange_interrupted", saved["error"])
        self.assertNotIn("active_operation", self.store.get("ACCOUNT#" + operation["email"]))

    def test_failed_before_promotion_remains_in_setup_after_lock_release(self):
        operation, state = self.enroll()
        self.google.exchange.side_effect = WorkflowError(400, "consent_exchange_failed", "retry")
        with self.assertRaises(WorkflowError):
            self.service.callback(state, "code", "session")
        operations = self.service.setup("owner")["operations"]
        self.assertEqual(operation["id"], operations[0]["id"])
        self.assertEqual("failed", operations[0]["phase"])

    def test_saved_account_status_survives_operation_ttl_cleanup(self):
        operation, _ = self.enroll()
        self.store.items[operation["pk"]].update(phase="ready", status="succeeded", verified_at=123)
        self.store.release_account(operation["email"], operation["id"])
        self.store.items.pop(operation["pk"])
        operations = self.service.setup("owner")["operations"]
        self.assertEqual("ready", operations[0]["phase"])
        self.assertEqual("succeeded", operations[0]["status"])
        self.assertEqual(123, operations[0]["verified_at"])
        self.assertEqual([], self.service.setup("another-owner")["operations"])

    def test_stale_callback_cannot_claim_new_attempt(self):
        operation, state = self.enroll()
        original_get = self.store.get
        def racing_get(key):
            result = original_get(key)
            if key == operation["pk"]:
                self.store.items[key]["state_hash"] = "new-state"
            return result
        self.store.get = racing_get
        with self.assertRaises(WorkflowError):
            self.service.callback(state, "code", "session")
        self.google.exchange.assert_not_called()


class GoogleTrustTests(unittest.TestCase):
    def test_web_client_rejects_installed_or_wrong_callback(self):
        for document in ({"installed": {}}, {"web": {"client_id": "a", "client_secret": "b", "redirect_uris": []}}):
            with self.assertRaises(WorkflowError):
                validate_web_client(document, "https://app.test/callback")

    def test_identity_requires_verified_email_nonce_scope_and_subject(self):
        google = GoogleClient(Mock())
        claims = {"sub": "subject", "email": "jay@example.com", "email_verified": True, "nonce": "nonce"}
        tokens = {"id_token": "signed", "scope": " ".join(SCOPES)}
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
            self.assertEqual("subject", google.identity(tokens, {"client_id": "client"}, "nonce", "jay@example.com"))
            for nonce, email, subject in (("wrong", "jay@example.com", None), ("nonce", "other@example.com", None), ("nonce", "jay@example.com", "old")):
                with self.assertRaises(WorkflowError):
                    google.identity(tokens, {"client_id": "client"}, nonce, email, subject)
            with self.assertRaises(WorkflowError):
                google.identity({**tokens, "scope": "openid email"}, {"client_id": "client"}, "nonce", "jay@example.com")

    def test_candidate_refreshes_even_with_warm_token_and_accepts_empty_mailbox(self):
        import credentials
        http = Mock()
        http.post.return_value = Mock(status_code=200, json=lambda: {"access_token": "fresh", "expires_in": 3600})
        http.get.side_effect = [Mock(status_code=200, json=lambda: {"emailAddress": "jay@example.com"}),
                               Mock(status_code=200, json=lambda: {"resultSizeEstimate": 0})]
        config = {"type": "oauth_refresh", "client_id": "c", "client_secret": "s", "refresh_token": "r", "scopes": SCOPES}
        credentials.mint_access_token("jay@example.com", config, http)
        GoogleClient(http).validate_candidate("jay@example.com", config)
        self.assertEqual(2, http.post.call_count)
        self.assertEqual(2, http.get.call_count)
        credentials._TOKEN_CACHE.clear()

    def test_secret_writes_are_managed_key_secure_strings(self):
        ssm = Mock()
        Store(Mock(), ssm, "/test").secret_put("client", {"secret": "private"})
        self.assertEqual("SecureString", ssm.put_parameter.call_args.kwargs["Type"])
        self.assertEqual("alias/aws/ssm", ssm.put_parameter.call_args.kwargs["KeyId"])

    def test_operation_identifier_contract(self):
        self.assertRegex(new_id(), r"^op_[1-9A-HJ-NP-Za-km-z]{22}$")
