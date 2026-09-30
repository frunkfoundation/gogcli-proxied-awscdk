"""Owner account/project contracts; installed state is independent of expiring attempts."""
import json
import os
import re
import time

from onboarding_google import GoogleClient, validate_web_client
from onboarding_services import (DEFAULT_SERVICES, KNOWN_ACCOUNTS, SERVICE_OPTIONS, account_projection,
                                 content_hash, public_operation, selected_services)
from onboarding_store import Store, WorkflowError, digest, new_id


def email_address(value):
    email = str(value).strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@/]+@[^\s@/]+\.[^\s@/]+", email):
        raise WorkflowError(400, "invalid_email", "Enter a valid Google account email address.")
    return email


def project_id(value):
    project = str(value).strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise WorkflowError(400, "invalid_project", "Enter the Google project ID.")
    return project


class OnboardingService:
    def __init__(self, store, google, callback_url):
        self.store, self.google, self.callback_url = store, google, callback_url

    def setup(self, owner, session_id=None):
        config = self.store.get("SETUP") or {}
        contexts = json.loads(os.getenv("GOOGLE_EXISTING_PROJECTS", "[]"))
        default = config.get("default_project_id", "jayj-gogcli-full-access")
        records = {item["project_id"]: item for item in self.store.rows("CLIENT#")}
        context_map = {item["projectId"]: item for item in contexts}
        projects = []
        for name in sorted(set(records) | set(context_map) | {default}):
            record, context = records.get(name, {}), context_map.get(name, {})
            projects.append({"projectId": name, "clientConfigured": bool(record.get("secret_ref")),
                "clientRevision": record.get("revision", ""), "clientId": record.get("client_id", ""), "publishingConfirmed": record.get("publishing_confirmed", False),
                "apiConfirmed": record.get("api_confirmed", {}), "recordedClientType": context.get("clientType", ""),
                "recordedClientId": context.get("clientId", "")})
        accounts = {item["email"]: item for item in self.store.accounts() if item.get("owner") == owner}
        for email in KNOWN_ACCOUNTS:
            accounts.setdefault(email, {"email": email, "connection_status": "not_connected",
                "selected_project_id": default, "requested_services": DEFAULT_SERVICES, "desired_owner_access": True})
        return {"callbackUrl": self.callback_url, "proxyUrl": os.getenv("GOG_PROXY_URL", ""),
            "setupUrl": os.getenv("ONBOARDING_BASE_URL", ""), "defaultProjectId": default,
            "ownerCallerArn": os.getenv("ONBOARDING_OWNER_CALLER_ARN", ""), "serviceOptions": SERVICE_OPTIONS,
            "projects": projects, "accounts": [self.projection(item, session_id) for item in sorted(accounts.values(), key=lambda row: row["email"])]}

    def projection(self, account, session_id=None):
        result = account_projection(account)
        latest = self.store.get("OP#" + account["latest_operation_id"]) if account.get("latest_operation_id") else None
        summary = public_operation(latest) if latest else account.get("latest_operation")
        if latest and latest["phase"] == "authorizing":
            summary.update(continuation_key=latest.get("command_key", ""), continuation_request=latest.get("command_request", {}),
                consent_session_matches=bool(session_id and latest.get("session_hash") == digest(session_id)))
        result.update(selected_project_id=account.get("selected_project_id", ""),
            installed_project_id=account.get("installed_project_id", ""), requested_services=account.get("requested_services", DEFAULT_SERVICES),
            owner_access_enabled=account.get("desired_owner_access", True),
            recovery_available=account.get("saved_recovery", {}).get("expires_at", 0) > int(time.time()),
            latest_operation=summary)
        return result

    def update_setup(self, owner, body):
        self.store.put({"pk": "SETUP", "owner": owner, "default_project_id": project_id(body.get("defaultProjectId", ""))})
        return self.setup(owner)

    def save_client(self, owner, project, web_json):
        project = project_id(project)
        client = validate_web_client(web_json, self.callback_url)
        if client["project_id"] != project:
            raise WorkflowError(400, "project_mismatch", "Upload the Web client from the selected Google project.")
        revision = new_id("cr")
        ref = self.store.secret_put("clients/" + revision, client)
        self.store.put({"pk": "CLIENT#" + project, "owner": owner, "revision": revision, "project_id": project,
            "secret_ref": ref, "client_id": client["client_id"], "publishing_confirmed": False, "api_confirmed": {}})
        return self.setup(owner)

    def update_project(self, owner, project, body):
        project = project_id(project)
        client = self.store.get("CLIENT#" + project)
        if not client or body.get("clientRevision") != client["revision"]:
            raise WorkflowError(409, "project_revision_changed", "Reload the project's current Web client before saving its review.")
        confirmed = body.get("apiConfirmed", {})
        if not isinstance(confirmed, dict) or any(name not in DEFAULT_SERVICES or not isinstance(value, bool) for name, value in confirmed.items()):
            raise WorkflowError(400, "invalid_confirmation", "Confirm the selected service APIs with true or false values.")
        changes = {"publishing_confirmed": body.get("publishingConfirmed") is True, "api_confirmed": confirmed}
        # Revision precondition is checked by DynamoDB, not only by the earlier read.
        self.store.confirm_project(client, changes)
        return self.setup(owner)

    def account(self, owner, email):
        item = self.store.get("ACCOUNT#" + email_address(email))
        if not item or item.get("owner") != owner:
            raise WorkflowError(404, "not_found", "Saved account not found.")
        return item

    def save_account(self, owner, email, body):
        email = email_address(email)
        item = self.store.get("ACCOUNT#" + email)
        config = self.store.get("SETUP") or {}
        project = project_id(body.get("projectId") or (item or {}).get("selected_project_id") or config.get("default_project_id", "jayj-gogcli-full-access"))
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
        updated = {**item, "selected_project_id": project, "requested_services": services}
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

    def accept(self, owner, account, kind, phase, changes=None, details=None, idem=None, supersede=False):
        now, op_id = int(time.time()), new_id()
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
