"""Short, account-fenced installed credential, policy, and allowlist mutations."""
import os
import time

from onboarding_services import content_hash, service_policy
from onboarding_store import WorkflowError, account_parameter, encoded, new_id


def caller_arn(owner_caller):
    return os.environ["ONBOARDING_OWNER_CALLER_ARN" if owner_caller else "ONBOARDING_VERIFIER_ROLE_ARN"]


def allow_parameter(store, owner_caller):
    return store.prefix + "/allow/" + encoded(caller_arn(owner_caller))


def policy_parameter(store, email):
    return store.prefix + "/policy/account/" + encoded(email)


def validate_allowlist(store, email, enabled, owner_caller):
    current = store.optional_secret(allow_parameter(store, owner_caller)) or {}
    emails, domains = current.get("emails", []), current.get("domains", [])
    if not isinstance(emails, list) or not isinstance(domains, list):
        raise WorkflowError(409, "invalid_allowlist", "The managed caller allowlist requires operator repair.")
    if any(not isinstance(item, str) or not item.strip() for item in emails + domains):
        raise WorkflowError(409, "invalid_allowlist", "Repair malformed managed caller entries first.")
    emails = [item.lower().strip() for item in emails]
    if "*" in emails or domains:
        raise WorkflowError(409, "broad_allowlist_requires_operator", "A wildcard/domain permission covers this account; an operator must narrow it first.")
    return current


def set_allowlist(store, email, enabled, owner_caller):
    caller = caller_arn(owner_caller)
    key, lease, now = "ALLOWLIST#" + encoded(caller), new_id("ls"), int(time.time())
    # SSM has no compare-and-swap; use the existing per-caller metadata lease.
    store.allow_lock(key, lease, now)
    try:
        current = validate_allowlist(store, email, enabled, owner_caller)
        emails = {str(item).lower().strip() for item in current.get("emails", [])}
        if enabled:
            emails.add(email)
        else:
            emails.discard(email)
        store.parameter_put(allow_parameter(store, owner_caller), {**current, "emails": sorted(emails)})
    finally:
        store.allow_unlock(key, lease)


def assert_current(store, op, lease):
    account = store.get("ACCOUNT#" + op["email"]) or {}
    if account.get("generation") != op["generation"] or account.get("active_operation") != op["id"] or account.get("effect_lease_owner") != lease:
        raise WorkflowError(409, "operation_superseded", "A newer account action superseded these effects.")
    return account


def promote(store, op, now):
    lease = store.acquire_effect(op, now)
    try:
        account = assert_current(store, op, lease)
        if account.get("desired_connection") != "connected":
            raise WorkflowError(409, "operation_superseded", "Account access was withdrawn.")
        candidate = store.secret_get(op["candidate_ref"])
        if candidate.get("type") != "oauth_refresh" or not all(candidate.get(name) for name in ("client_id", "client_secret", "refresh_token")):
            raise WorkflowError(400, "invalid_candidate", "Saved candidate authorization is incomplete; restart consent.")
        policy = service_policy(op["services"])
        policy_hash = content_hash(policy)
        current = store.optional_secret(policy_parameter(store, op["email"]))
        if current is not None and content_hash(current) not in {account.get("managed_policy_hash"), op.get("policy_write_hash")}:
            raise WorkflowError(409, "operator_policy_requires_review", "An operator changed this account policy; reconcile its service ceiling before recovering.")
        if not op.get("policy_write_hash"):
            op = store.transition(op["pk"], op["phase"], {"policy_write_hash": policy_hash},
                conditions={"generation": op["generation"]})
        # On interruption the operation's planned hash recognizes its own policy write.
        assert_current(store, op, lease)
        store.parameter_put(policy_parameter(store, op["email"]), policy)
        assert_current(store, op, lease)
        store.parameter_put(account_parameter(store.prefix, op["email"]), candidate)
        assert_current(store, op, lease)
        set_allowlist(store, op["email"], True, owner_caller=False)
        account = assert_current(store, op, lease)
        set_allowlist(store, op["email"], account.get("desired_owner_access", False), owner_caller=True)
        store.update_account(op, {"connection_status": "connected", "credential_revision": candidate["credential_revision"],
            "installed_project_id": op["project_id"], "installed_client_revision": op["client_revision"],
            "installed_services": op["services"], "granted_scopes": op["granted_scopes"], "google_subject": op["google_subject"],
            "managed_policy_hash": policy_hash, "verifications": {}, "saved_recovery": {}}, lease=lease)
        written_at = int(time.time())
        return {"phase": "verifying", "promoted_at": written_at, "credential_revision": candidate["credential_revision"],
            "next_attempt": written_at + 65, "authorization_written_at": written_at, "propagation_complete_at": written_at + 65}
    finally:
        store.release_effect(op["email"], lease)


def apply_access(store, op, now):
    lease = store.acquire_effect(op, now)
    try:
        account = assert_current(store, op, lease)
        enabled = account.get("desired_owner_access", False)
        if enabled and not account.get("credential_revision"):
            raise WorkflowError(409, "connection_required", "Connect the account before enabling caller access.")
        set_allowlist(store, op["email"], enabled, owner_caller=True)
        assert_current(store, op, lease)
        written_at = int(time.time())
        return {"phase": "propagating", "authorization_written_at": written_at,
            "propagation_complete_at": written_at + 65, "next_attempt": written_at + 65}
    finally:
        store.release_effect(op["email"], lease)


def remove_connection(store, op, now):
    lease = store.acquire_effect(op, now)
    try:
        account = assert_current(store, op, lease)
        # Delete credentials first: all callers are denied even if an allowlist needs repair.
        store.secret_delete(account_parameter(store.prefix, op["email"]))
        assert_current(store, op, lease)
        for owner_caller in (True, False):
            set_allowlist(store, op["email"], False, owner_caller)
            assert_current(store, op, lease)
        policy = store.optional_secret(policy_parameter(store, op["email"]))
        if policy and content_hash(policy) == account.get("managed_policy_hash"):
            store.secret_delete(policy_parameter(store, op["email"]))
        for field in ("candidate_ref", "callback_ref", "attempt_ref"):
            store.secret_delete(account.get("saved_recovery", {}).get(field))
        for reference in op.get("cleanup_refs", []):
            store.secret_delete(reference)
        store.update_account(op, {"credential_revision": "", "installed_services": [], "granted_scopes": [],
            "installed_project_id": "", "installed_client_revision": "", "verifications": {}, "saved_recovery": {},
            "managed_policy_hash": "" if not policy or content_hash(policy) == account.get("managed_policy_hash") else account.get("managed_policy_hash", "")}, lease=lease)
        written_at = int(time.time())
        return {"phase": "propagating", "authorization_written_at": written_at,
            "propagation_complete_at": written_at + 65, "next_attempt": written_at + 65}
    finally:
        store.release_effect(op["email"], lease)
