"""Single-application account contracts and operator-owned client configuration."""
import os
import re
import time

from onboarding_google import GoogleClient, validate_web_client
from onboarding_services import (DEFAULT_SERVICES, SCOPES_BY_SERVICE, SERVICE_OPTIONS, account_projection,
                                 content_hash, public_operation, selected_services)
from onboarding_store import Store, WorkflowError, digest, new_id


def email_address(value):
    email = str(value).strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@/]+@[^\s@/]+\.[^\s@/]+", email):
        raise WorkflowError(400, "invalid_email", "Enter a valid Google account email address.")
    return email


def application_project_id():
    project = os.environ.get("GOOGLE_PROJECT_ID")
    if project != "gogcli-proxy":
        raise WorkflowError(503, "application_not_configured", "gogcli-proxy is not ready. Contact the operator, then retry.")
    return project


class OnboardingService:
    def __init__(self, store, google, callback_url):
        self.store, self.google, self.callback_url = store, google, callback_url

    def application(self):
        project = application_project_id()
        client = self.store.get("CLIENT#" + project) or {}
        return {"projectId": project, "name": os.environ.get("GOOGLE_APPLICATION_NAME", "gogcli-proxy"),
            "organization": os.environ.get("GOOGLE_APPLICATION_ORGANIZATION", "kraecollective.com"),
            "clientConfigured": bool(client.get("secret_ref") and client.get("revision")), "apiConfirmed": client.get("api_confirmed", {})}

    def setup(self, owner, session_id=None):
        accounts = [item for item in self.store.accounts() if item.get("owner") == owner]
        return {"application": self.application(), "serviceOptions": SERVICE_OPTIONS,
            "accounts": [self.projection(item, session_id) for item in sorted(accounts, key=lambda row: row["email"])]}

    def projection(self, account, session_id=None):
        result = account_projection(account)
        latest = self.store.get("OP#" + account["latest_operation_id"]) if account.get("latest_operation_id") else None
        summary = public_operation(latest) if latest else account.get("latest_operation")
        if latest and latest["phase"] == "authorizing" and latest.get("auth_mode", "owner") == "owner":
            summary.update(continuation_key=latest.get("command_key", ""), continuation_request=latest.get("command_request", {}),
                consent_session_matches=bool(session_id and latest.get("session_hash") == digest(session_id)))
        if latest and latest.get("auth_mode") == "connection":
            summary.update(auth_mode="connection", connection_link_id=latest["connection_link_id"],
                connection_expires_at=latest["link_expires_at"])
        result.update(uses_current_app=bool(account.get("credential_revision") and account.get("installed_project_id") == application_project_id()),
            requested_services=account.get("requested_services", DEFAULT_SERVICES),
            owner_access_enabled=account.get("desired_owner_access", True),
            recovery_available=account.get("saved_recovery", {}).get("expires_at", 0) > int(time.time()),
            latest_operation=summary)
        return result

    def application_configuration(self, client=None):
        if client is None:
            client = self.store.get("CLIENT#" + application_project_id()) or {}
        return {**self.application(), "clientConfigured": bool(client.get("secret_ref") and client.get("revision")),
            "apiConfirmed": client.get("api_confirmed", {}), "clientRevision": client.get("revision", ""),
            "publishingStatus": client.get("publishing_status", "unknown")}

    def import_client(self, web_json):
        project = application_project_id()
        client = validate_web_client(web_json, self.callback_url)
        if client["project_id"] != project:
            raise WorkflowError(400, "project_mismatch", "Supply the gogcli-proxy application's Web client.")
        existing = self.store.get("CLIENT#" + project)
        if existing and self.store.optional_secret(existing.get("secret_ref")) == client:
            return self.application_configuration(existing)
        revision = new_id("cr")
        ref = self.store.secret_put("clients/" + revision, client)
        record = {"pk": "CLIENT#" + project, "revision": revision, "project_id": project,
            "secret_ref": ref, "client_id": client["client_id"], "publishing_status": "unknown", "api_confirmed": {}}
        self.store.put(record)
        return self.application_configuration(record)

    def review_application(self, body):
        client = self.store.get("CLIENT#" + application_project_id())
        if not client or body.get("clientRevision") != client["revision"]:
            raise WorkflowError(409, "project_revision_changed", "Reload the application's current Web client before saving its review.")
        confirmed = body.get("apiConfirmed", {})
        if not isinstance(confirmed, dict) or any(name not in SCOPES_BY_SERVICE or not isinstance(value, bool) for name, value in confirmed.items()):
            raise WorkflowError(400, "invalid_confirmation", "Confirm the selected service APIs with true or false values.")
        publishing = body.get("publishingStatus", "unknown")
        if publishing not in {"testing", "production", "unknown"}:
            raise WorkflowError(400, "invalid_confirmation", "Record publishing status as testing, production, or unknown.")
        changes = {"publishing_status": publishing, "api_confirmed": confirmed}
        self.store.confirm_project(client, changes)
        return self.application_configuration({**client, **changes})

    def account(self, owner, email):
        item = self.store.get("ACCOUNT#" + email_address(email))
        if not item or item.get("owner") != owner:
            raise WorkflowError(404, "not_found", "Saved account not found.")
        return item

    def save_account(self, owner, email, body):
        if set(body) - {"services"}:
            raise WorkflowError(400, "invalid_account", "Supply only service choices. Reload the account page.")
        application_project_id()
        email = email_address(email)
        item = self.store.get("ACCOUNT#" + email)
        services = selected_services(body.get("services", (item or {}).get("requested_services", DEFAULT_SERVICES)))
        if item:
            if item.get("owner") != owner:
                raise WorkflowError(404, "not_found", "Saved account not found.")
            if item.get("active_operation"):
                raise WorkflowError(409, "operation_conflict", "Finish the current account action before changing setup.")
            if not set(item.get("installed_services", [])).issubset(services):
                raise WorkflowError(409, "scope_removal_requires_policy", "Narrow caller policy or disconnect; reconnect preserves installed services.")
        else:
            item = {"pk": "ACCOUNT#" + email, "email": email, "owner": owner, "generation": 0,
                "connection_status": "not_connected", "desired_connection": "connected", "desired_owner_access": True}
        updated = {**item, "requested_services": services}
        self.store.save_account_metadata(item, updated)
        return self.projection(updated)

    def operation(self, owner, operation_id):
        op = self.store.get("OP#" + operation_id)
        if not op or op.get("owner") != owner or op.get("expires_at", int(time.time()) + 1) <= int(time.time()):
            raise WorkflowError(404, "not_found", "Operation details expired; use the saved account controls.")
        return public_operation(op)

    def replay(self, owner, email, kind, key, request):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise WorkflowError(400, "idempotency_required", "Supply a new Idempotency-Key for this account command.")
        pk = "IDEM#" + digest(self.store.prefix + ":" + owner + ":" + email + ":" + kind + ":" + key)
        fingerprint = content_hash(request)
        item = self.store.get(pk)
        if item:
            if item.get("expires_at", int(time.time()) + 1) <= int(time.time()):
                raise WorkflowError(409, "idempotency_expired", "Use a new request key for newly intended account work.")
            if item["fingerprint"] != fingerprint:
                raise WorkflowError(409, "idempotency_conflict", "This request key was used for different account choices.")
            op = self.store.get("OP#" + item["operation_id"])
            if op:
                return pk, fingerprint, public_operation(op)
            if item.get("result"):
                return pk, fingerprint, item["result"]
            raise WorkflowError(409, "operation_unavailable", "Refresh the saved account before retrying this command.")
        return pk, fingerprint, None

    def accept(self, owner, account, kind, phase, changes=None, details=None, idem=None, supersede=False, operation_id=None):
        now, op_id = int(time.time()), operation_id or new_id()
        op = {"pk": "OP#" + op_id, "id": op_id, "owner": owner, "email": account["email"], "kind": kind,
            "phase": phase, "status": "accepted", "generation": int(account["generation"]) + 1,
            "created_at": now, "updated_at": now, "attempts": 0, "next_attempt": now, **(details or {})}
        key = None
        if idem:
            pk, fingerprint = idem
            op.update(idempotency_pk=pk, request_fingerprint=fingerprint)
            key = {"pk": pk, "operation_id": op_id, "fingerprint": fingerprint}
        try:
            self.store.accept(account, op, key, changes or {}, supersede=supersede)
        except WorkflowError:
            if key:
                existing = self.store.get(key["pk"])
                if existing and existing["fingerprint"] == key["fingerprint"]:
                    accepted = self.store.get("OP#" + existing["operation_id"]) or existing.get("result")
                    return {**accepted, "accepted_elsewhere": True}
            raise
        return op

    def enroll(self, owner, email, key, session_id, body=None):
        from onboarding_commands import enroll
        return enroll(self, owner, email_address(email), key, session_id, body or {})

    def verification(self, owner, email, key):
        from onboarding_commands import verification
        return verification(self, owner, email_address(email), key)

    def recover(self, owner, email, key):
        from onboarding_commands import recover
        return recover(self, owner, email_address(email), key)

    def access(self, owner, email, enabled):
        from onboarding_commands import access
        return access(self, owner, email_address(email), enabled)

    def disconnect(self, owner, email):
        from onboarding_commands import disconnect
        return disconnect(self, owner, email_address(email))

    def callback(self, state, code, session_id, error=None):
        from onboarding_consent import callback
        return callback(self, state, code, session_id, error)

    def validate_candidate_operation(self, op):
        from onboarding_consent import validate_candidate
        return validate_candidate(self, op)


def build_service():
    import boto3
    import requests
    from botocore.config import Config
    config = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1})
    table = boto3.resource("dynamodb", config=config).Table(os.environ["ONBOARDING_TABLE"])
    store = Store(table, boto3.client("ssm", config=config), os.environ.get("GOG_SSM_PREFIX", "/gogcli-proxy"))
    return OnboardingService(store, GoogleClient(requests.Session()), os.environ["GOOGLE_CALLBACK_URL"])
