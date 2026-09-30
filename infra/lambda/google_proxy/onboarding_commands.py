"""Durable account commands, including restrictive intent that supersedes old work."""
import time

from onboarding_services import public_operation, selected_services
from onboarding_store import WorkflowError, digest


def enroll(service, owner, email, key, session_id, body):
    from onboarding_consent import consent_response, start_consent
    pk, fingerprint, replay = service.replay(owner, email, "enrollment", key, body)
    if replay:
        op = service.store.get("OP#" + replay["id"])
        return consent_response(service, op, session_id) if op and op["phase"] == "authorizing" else replay
    if set(body) - {"services", "ownerAccessEnabled", "restart"}:
        raise WorkflowError(400, "invalid_enrollment", "Supply only services, owner access, and restart choices. Reload the account page.")
    if "restart" in body and not isinstance(body["restart"], bool):
        raise WorkflowError(400, "invalid_enrollment", "Restart must be true or false.")
    account = service.account(owner, email)
    if account.get("desired_connection") == "disconnected" and account.get("connection_status") == "disconnecting":
        raise WorkflowError(409, "disconnect_in_progress", "Wait for removal before explicitly reconnecting.")
    from onboarding_operations import application_project_id
    project = application_project_id()
    services = selected_services(body.get("services", account.get("installed_services") or account["requested_services"]))
    if not set(account.get("installed_services", [])).issubset(services):
        raise WorkflowError(409, "scope_removal_requires_policy", "Reconnect preserves installed services; narrow caller policy or disconnect.")
    client = service.store.get("CLIENT#" + project)
    if not client or not client.get("secret_ref") or not client.get("revision") or any(not client.get("api_confirmed", {}).get(name) for name in services):
        raise WorkflowError(409, "application_setup_required", "gogcli-proxy is not ready to connect this account. Contact the operator, then retry.")
    enabled = account.get("desired_owner_access", True)
    if "ownerAccessEnabled" in body:
        if not isinstance(body["ownerAccessEnabled"], bool):
            raise WorkflowError(400, "invalid_access", "Owner access must be true or false.")
        if body["ownerAccessEnabled"] != enabled and account.get("credential_revision"):
            raise WorkflowError(409, "access_action_required", "Use the separate tool-access action to change saved account access.")
    old = service.store.get("OP#" + account["active_operation"]) if account.get("active_operation") else None
    supersede = False
    if old:
        if old["phase"] not in {"authorizing", "preparing_consent"}:
            raise WorkflowError(409, "operation_conflict", "The saved authorization is being checked; wait or disconnect.")
        same_session = old.get("session_hash") == digest(session_id)
        if old["phase"] == "preparing_consent" and old.get("next_attempt", 0) > int(time.time()) and same_session and not body.get("restart", False):
            raise WorkflowError(409, "operation_conflict", "Consent is being prepared; refresh saved progress or explicitly restart.")
        if old["phase"] == "authorizing" and old.get("consent_expires_at", 0) > int(time.time()) and same_session and not body.get("restart", False):
            raise WorkflowError(409, "operation_conflict", "Continue the existing consent using its original request key, or explicitly restart.")
        supersede = True
    elif account.get("active_operation"):
        raise WorkflowError(409, "operation_conflict", "Refresh the account's active work before retrying.")
    operation = service.accept(owner, account, "enrollment", "preparing_consent",
        changes={"desired_connection": "connected", "desired_owner_access": enabled,
                 "requested_services": services},
        details={"client_revision": client["revision"], "client_ref": client["secret_ref"], "project_id": project,
                 "services": services, "session_hash": digest(session_id), "command_key": key, "command_request": body, "next_attempt": int(time.time()) + 120},
        idem=(pk, fingerprint), supersede=supersede)
    if operation.get("accepted_elsewhere") or operation["phase"] != "preparing_consent":
        return public_operation(operation)
    return start_consent(service, operation, session_id)


def verification(service, owner, email, key):
    pk, fingerprint, replay = service.replay(owner, email, "verification", key, {})
    if replay:
        return replay
    account = service.account(owner, email)
    if not account.get("credential_revision") or account.get("connection_status") in {"disconnecting", "disconnected"}:
        raise WorkflowError(409, "connection_required", "Connect this account before checking installed authorization.")
    op = service.accept(owner, account, "verification", "verifying", details={"services": account["installed_services"],
        "credential_revision": account["credential_revision"]}, idem=(pk, fingerprint))
    return public_operation(op)


def recover(service, owner, email, key):
    pk, fingerprint, replay = service.replay(owner, email, "recovery", key, {})
    if replay:
        return replay
    account = service.account(owner, email)
    recovery = account.get("saved_recovery")
    if account.get("desired_connection") == "disconnected" or not recovery or recovery.get("expires_at", 0) <= int(time.time()):
        raise WorkflowError(409, "consent_required", "No usable saved authorization remains; start fresh Google consent.")
    source_ref = recovery.get("candidate_ref") or recovery.get("callback_ref")
    saved = service.store.optional_secret(source_ref) if source_ref else None
    if not saved:
        raise WorkflowError(409, "consent_required", "Saved authorization expired; start fresh Google consent.")
    details = {name: recovery[name] for name in ("client_ref", "client_revision", "project_id", "services", "google_subject", "granted_scopes", "policy_write_hash") if name in recovery}
    op = service.accept(owner, account, "recovery", "preparing_recovery", details={**details,
        "recovery_source_ref": source_ref, "recovery_source_kind": "candidate" if recovery.get("candidate_ref") else "callback"}, idem=(pk, fingerprint))
    from onboarding_worker import prepare_recovery
    changes = prepare_recovery(service.store, op, int(time.time()))
    op = service.store.transition(op["pk"], "preparing_recovery", changes)
    return public_operation(op)


def access(service, owner, email, enabled):
    if not isinstance(enabled, bool):
        raise WorkflowError(400, "invalid_access", "Access enabled must be true or false.")
    account = service.account(owner, email)
    active = service.store.get("OP#" + account["active_operation"]) if account.get("active_operation") else None
    if active and active["kind"] == "disconnect":
        if not enabled:
            return public_operation(active)
        raise WorkflowError(409, "disconnect_in_progress", "Wait for removal before enabling caller access.")
    if active and active["kind"] == "access" and account.get("desired_owner_access") == enabled:
        return public_operation(active)
    if enabled and (not account.get("credential_revision") or account.get("desired_connection") == "disconnected"):
        raise WorkflowError(409, "connection_required", "Connect this account before enabling caller access.")
    from onboarding_effects import validate_allowlist
    validate_allowlist(service.store, email, enabled, owner_caller=True)
    op = service.accept(owner, account, "access", "applying_access", changes={"desired_owner_access": enabled},
        details={"enabled": enabled}, supersede=not enabled)
    return public_operation(op)


def disconnect(service, owner, email):
    account = service.account(owner, email)
    active = service.store.get("OP#" + account["active_operation"]) if account.get("active_operation") else None
    if active and active["kind"] == "disconnect":
        return public_operation(active)
    if account.get("connection_status") == "disconnected" and not account.get("credential_revision"):
        return {"email": email, "kind": "disconnect", "phase": "disconnected", "status": "succeeded"}
    op = service.accept(owner, account, "disconnect", "removing", changes={"desired_connection": "disconnected",
        "desired_owner_access": False, "connection_status": "disconnecting"},
        details={"cleanup_refs": [active[field] for field in ("candidate_ref", "callback_ref", "attempt_ref") if active and active.get(field)]},
        supersede=True)
    return public_operation(op)
