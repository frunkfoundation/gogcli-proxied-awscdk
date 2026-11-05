"""Account contracts: installed access survives attempts, expiry, and restrictive commands."""
import copy
import os
import unittest
from unittest.mock import Mock, patch

from onboarding_operations import OnboardingService
from onboarding_store import WorkflowError


class MemoryStore:
    def __init__(self):
        self.items, self.secrets, self.prefix = {}, {}, "/test"

    def get(self, key):
        return copy.deepcopy(self.items.get(key))

    def put(self, item, absent=False):
        if absent and item["pk"] in self.items:
            raise WorkflowError(409, "operation_conflict", "conflict")
        self.items[item["pk"]] = copy.deepcopy(item)

    def rows(self, prefix):
        return [self.get(key) for key in self.items if key.startswith(prefix)]

    def accounts(self):
        return self.rows("ACCOUNT#")

    def secret_put(self, suffix, value):
        name = self.prefix + "/onboarding/" + suffix
        self.secrets[name] = copy.deepcopy(value)
        return name

    def secret_get(self, name):
        if name not in self.secrets:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return copy.deepcopy(self.secrets[name])

    def secret_delete(self, name):
        self.secrets.pop(name, None)

    def parameter_put(self, name, value):
        self.secrets[name] = copy.deepcopy(value)

    def optional_secret(self, name):
        return copy.deepcopy(self.secrets.get(name))

    def confirm_project(self, client, changes):
        if self.get(client["pk"])["revision"] != client["revision"]:
            raise WorkflowError(409, "operation_conflict", "revision changed")
        self.put({**client, **changes})

    def save_account_metadata(self, original, updated):
        current = self.get(original["pk"])
        if current and (current["generation"] != original["generation"] or current.get("active_operation")):
            raise WorkflowError(409, "operation_conflict", "busy")
        self.put(updated)

    def claim_operation(self, operation, lease, now):
        current = self.get(operation["pk"])
        if current["phase"] != operation["phase"] or current.get("lease_until", 0) > now:
            raise WorkflowError(409, "operation_conflict", "claimed")
        current.update(lease_until=now + 60, lease_owner=lease)
        self.put(current)
        return current

    def finish_operation(self, operation, lease, changes):
        current = self.get(operation["pk"])
        if current["phase"] != operation["phase"] or current.get("lease_owner") != lease:
            raise WorkflowError(409, "operation_conflict", "changed")
        current.update(changes)
        current.pop("lease_owner", None)
        current.pop("lease_until", None)
        self.put(current)
        return current

    def allow_lock(self, key, lease, now):
        item = self.get(key) or {"pk": key}
        if item.get("lease_until", 0) > now:
            raise WorkflowError(409, "operation_conflict", "allowlist busy")
        self.put({**item, "lease_until": now + 60, "lease_owner": lease})

    def allow_unlock(self, key, lease):
        item = self.get(key)
        if item.get("lease_owner") != lease:
            raise WorkflowError(409, "operation_conflict", "lease lost")
        item.pop("lease_until", None)
        item.pop("lease_owner", None)
        self.put(item)

    def accept(self, account, operation, idem, changes, supersede=False):
        current = self.get(account["pk"])
        if current["generation"] != account["generation"] or (current.get("active_operation") and not supersede):
            raise WorkflowError(409, "operation_conflict", "busy")
        if idem and idem["pk"] in self.items:
            raise WorkflowError(409, "operation_conflict", "duplicate")
        old_id = current.get("active_operation")
        current.update(changes, generation=operation["generation"], active_operation=operation["id"], latest_operation_id=operation["id"])
        if supersede and old_id:
            old = self.get("OP#" + old_id)
            if old:
                old.update(phase="canceled", status="canceled")
                self.put(old)
        self.put(current)
        self.put(operation, absent=True)
        if idem:
            self.put(idem, absent=True)

    def transition(self, key, expected, changes, conditions=None):
        item = self.get(key)
        if not item or item.get("phase") != expected or any(item.get(k) != v for k, v in (conditions or {}).items()):
            raise WorkflowError(409, "operation_conflict", "changed")
        item.update(changes)
        self.put(item)
        return item

    def update_account(self, operation, changes, lease=None):
        item = self.get("ACCOUNT#" + operation["email"])
        if item.get("generation") != operation["generation"] or item.get("active_operation") != operation["id"]:
            raise WorkflowError(409, "operation_superseded", "superseded")
        if lease and item.get("effect_lease_owner") != lease:
            raise WorkflowError(409, "operation_superseded", "lease lost")
        item.update(changes)
        self.put(item)
        return item

    def acquire_effect(self, operation, now):
        item = self.update_account(operation, {})
        if item.get("effect_lease_until", 0) > now:
            raise WorkflowError(409, "effect_busy", "busy")
        lease = "lease-" + operation["id"]
        item.update(effect_lease_owner=lease, effect_lease_until=now + 60)
        self.put(item)
        return lease

    def release_effect(self, email, lease):
        item = self.get("ACCOUNT#" + email)
        if item.get("effect_lease_owner") == lease:
            item.pop("effect_lease_owner", None)
            item.pop("effect_lease_until", None)
            self.put(item)

    def terminal(self, operation, changes, now):
        op = self.get(operation["pk"])
        if op["phase"] != operation["phase"]:
            return
        op.update(changes, updated_at=now, expires_at=now + 604800)
        self.put(op)
        if op.get("idempotency_pk"):
            from onboarding_services import public_operation
            idem = self.get(op["idempotency_pk"])
            idem.update(result=public_operation(op), expires_at=now + 604800)
            self.put(idem)
        account = self.get("ACCOUNT#" + op["email"])
        if account.get("active_operation") == op["id"] and account["generation"] == op["generation"]:
            from onboarding_services import public_operation
            account.pop("active_operation", None)
            account["latest_operation"] = public_operation(op)
            self.put(account)


class AccountLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.google = Mock()
        self.google.authorization_url.return_value = "https://accounts.google.com/consent"
        self.service = OnboardingService(self.store, self.google, "https://app.example/onboarding/oauth/google/callback")
        self.env = patch.dict(os.environ, {"ONBOARDING_OWNER_CALLER_ARN": "arn:aws:iam::123456789012:role/owner",
            "ONBOARDING_VERIFIER_ROLE_ARN": "arn:aws:iam::123456789012:role/verifier", "GOOGLE_PROJECT_ID": "gogcli-proxy"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.service.import_client( {"web": {"client_id": "client", "client_secret": "secret",
            "project_id": "gogcli-proxy", "redirect_uris": [self.service.callback_url]}})
        revision = self.store.get("CLIENT#gogcli-proxy")["revision"]
        self.service.review_application( {"clientRevision": revision, "publishingStatus": "production",
            "apiConfirmed": {"gmail": True, "drive": True}})
        self.service.save_account("owner", "jay@example.com", {"services": ["gmail", "drive"]})

    def enroll(self, key="first", **body):
        return self.service.enroll("owner", "jay@example.com", key, "session", body)

    def test_installed_connection_survives_failed_reconnect_and_operation_cleanup(self):
        account = self.store.get("ACCOUNT#jay@example.com")
        account.update(connection_status="connected", credential_revision="installed", installed_services=["gmail"],
            granted_scopes=["https://www.googleapis.com/auth/gmail.modify"], desired_owner_access=False)
        self.store.put(account)
        op = self.enroll()
        current = self.store.get("OP#" + op["id"])
        self.store.terminal(current, {"phase": "failed", "status": "failed", "error_code": "consent_denied"}, 100)
        self.store.items.pop(current["pk"])
        projected = next(a for a in self.service.setup("owner")["accounts"] if a["email"] == "jay@example.com")
        self.assertEqual("connected", projected["connection_status"])
        self.assertFalse(projected["owner_access_enabled"])
        self.assertEqual("failed", projected["latest_operation"]["status"])
        self.assertNotEqual(op["id"], self.enroll(key="new")["id"])

    def test_application_confirmation_is_revision_bound_and_new_import_clears_review(self):
        old_revision = self.store.get("CLIENT#gogcli-proxy")["revision"]
        self.service.import_client( {"web": {"client_id": "new", "client_secret": "secret",
            "project_id": "gogcli-proxy", "redirect_uris": [self.service.callback_url]}})
        with self.assertRaises(WorkflowError) as failed:
            self.service.review_application( {"clientRevision": old_revision, "publishingStatus": "production"})
        self.assertEqual("project_revision_changed", failed.exception.code)
        self.assertEqual({}, self.service.setup("owner")["application"]["apiConfirmed"])

    def test_idempotent_enrollment_replays_without_recreating_consent_and_detects_changed_data(self):
        op = self.enroll()
        self.assertEqual(op["id"], self.enroll()["id"])
        self.assertEqual(1, self.google.authorization_url.call_count)
        with self.assertRaises(WorkflowError) as failed:
            self.enroll(services=["drive"])
        self.assertEqual("idempotency_conflict", failed.exception.code)

    def test_access_disable_fences_enrollment_and_preserves_installed_state(self):
        op = self.enroll()
        result = self.service.access("owner", "jay@example.com", False)
        account = self.store.get("ACCOUNT#jay@example.com")
        self.assertFalse(account["desired_owner_access"])
        self.assertEqual("canceled", self.store.get("OP#" + op["id"])["status"])
        self.assertEqual("access", result["kind"])

    def test_disconnect_supersedes_active_consent_and_is_repeated_idempotently(self):
        old = self.enroll()
        result = self.service.disconnect("owner", "jay@example.com")
        self.assertEqual(result["id"], self.service.disconnect("owner", "jay@example.com")["id"])
        self.assertEqual("disconnected", self.store.get("ACCOUNT#jay@example.com")["desired_connection"])
        self.assertEqual("canceled", self.store.get("OP#" + old["id"])["status"])

    def test_session_mismatch_restarts_authorization_without_waiting_for_old_lock(self):
        old = self.enroll()
        new = self.service.enroll("owner", "jay@example.com", "new-session", "another-session", {})
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual("canceled", self.store.get("OP#" + old["id"])["status"])

    def test_terminal_idempotency_replays_after_operation_deleted(self):
        response = self.enroll()
        operation = self.store.get("OP#" + response["id"])
        self.store.terminal(operation, {"phase": "failed", "status": "failed"}, int(__import__("time").time()))
        self.store.items.pop(operation["pk"])
        replay = self.enroll()
        self.assertEqual("failed", replay["status"])
        self.assertEqual(response["id"], replay["id"])


if __name__ == "__main__":
    unittest.main()
