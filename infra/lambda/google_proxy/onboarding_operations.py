"""Owner-scoped enrollment and durable candidate promotion intents."""
import os
import re
import secrets
import time

from onboarding_google import GoogleClient, SCOPES, validate_web_client
from onboarding_store import Store, WorkflowError, account_parameter, digest, new_id


def _email(value):
    email = str(value).strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@/]+@[^\s@/]+\.[^\s@/]+", email):
        raise WorkflowError(400, "invalid_email", "Enter a valid mailbox email address.")
    return email


def public_operation(item):
    result = {key: item[key] for key in ("id", "email", "status", "phase", "error", "error_code", "recovery", "promoted_at", "verified_at") if key in item}
    return result


class OnboardingService:
    def __init__(self, store, google, callback_url):
        self.store, self.google, self.callback_url = store, google, callback_url

    def setup(self, owner):
        client = self.store.get("CLIENT") or {}
        config = self.store.get("SETUP") or {}
        accounts = self.store.accounts()
        operations = []
        for account in accounts:
            op_id = account.get("active_operation") or account.get("operation_id")
            op = self.store.get("OP#" + op_id) if op_id else None
            if op and op.get("owner") == owner:
                operations.append(public_operation(op))
            elif op_id and not op and account.get("owner") == owner and account.get("terminal_operation_id") == op_id:
                operations.append(public_operation({**account, "id": op_id}))
        return {"callbackUrl": self.callback_url, "configured": bool(client),
            "projectId": client.get("project_id", config.get("project_id", "")),
            "publishingConfirmed": config.get("publishing_confirmed", False),
            "emails": config.get("emails", ["jay.jakosky@gmail.com", "jay@frunkholdings.ltd", "treasurer@longmonttheatre.org"] + [""] * 5),
            "operations": operations, "proxyUrl": os.getenv("GOG_PROXY_URL", "")}

    def update_setup(self, owner, body):
        project = str(body.get("projectId", "")).strip()
        if project and not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
            raise WorkflowError(400, "invalid_project", "Enter the Google project ID.")
        emails = body.get("emails", [])
        if not isinstance(emails, list) or any(not isinstance(email, str) for email in emails):
            raise WorkflowError(400, "invalid_checklist", "Supply the mailbox checklist as a list of email addresses.")
        emails = [_email(email) if email.strip() else "" for email in emails]
        for email in emails:
            if email:
                self.add_mailbox(owner, email)
        self.store.put({"pk": "SETUP", "owner": owner, "project_id": project,
            "publishing_confirmed": body.get("publishingConfirmed") is True, "emails": emails})
        return self.setup(owner)

    def save_client(self, owner, web_json):
        client = validate_web_client(web_json, self.callback_url)
        revision = new_id("cr")
        ref = self.store.secret_put("clients/" + revision, client)
        self.store.put({"pk": "CLIENT", "owner": owner, "revision": revision,
            "project_id": client["project_id"], "secret_ref": ref})
        return self.setup(owner)

    def add_mailbox(self, owner, email):
        email = _email(email)
        existing = self.store.get("ACCOUNT#" + email)
        if not existing:
            self.store.put({"pk": "ACCOUNT#" + email, "owner": owner, "email": email}, absent=True)
        return email

    def enroll(self, owner, email, idempotency_key, session_id):
        email = self.add_mailbox(owner, email)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise WorkflowError(400, "idempotency_required", "Supply an idempotency key.")
        idem_key = "IDEM#" + digest(owner + ":" + idempotency_key)
        existing = self.store.get(idem_key)
        if existing:
            if existing["email"] != email:
                raise WorkflowError(409, "idempotency_conflict", "This request key belongs to a different mailbox.")
            return self.operation(owner, existing["operation_id"])
        client = self.store.get("CLIENT")
        if not client:
            raise WorkflowError(409, "client_required", "Import your Google Web client first.")
        now, op_id = int(time.time()), new_id()
        self.store.lock_account(email, op_id, now)
        try:
            operation = {"pk": "OP#" + op_id, "id": op_id, "owner": owner, "email": email,
                "phase": "authorizing", "status": "accepted", "created_at": now, "updated_at": now,
                "client_revision": client["revision"], "client_ref": client["secret_ref"],
                "candidate_ref": self.store.prefix + "/onboarding/candidates/" + op_id,
                "callback_ref": self.store.prefix + "/onboarding/callbacks/" + op_id}
            self.store.put(operation, absent=True)
            self.store.put({"pk": idem_key, "operation_id": op_id, "email": email}, absent=True)
            return self._consent(operation, session_id)
        except Exception:
            self.store.release_account(email, op_id)
            raise

    def _consent(self, operation, session_id):
        now = int(time.time())
        state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        ref = self.store.secret_put("attempts/" + operation["id"] + "/" + new_id("at"), {"verifier": verifier, "nonce": nonce})
        changes = {"phase": "authorizing", "status": "accepted", "state_hash": digest(state),
            "session_hash": digest(session_id), "attempt_ref": ref,
            "callback_ref": self.store.prefix + "/onboarding/callbacks/" + operation["id"] + "/" + digest(state), "consent_expires_at": now + 1800, "expires_at": now + 604800, "updated_at": now,
            "promoted_at": 0, "verified_at": 0, "attempts": 0, "next_attempt": 0,
            "exchange_saved": False, "validation_complete": False, "exchange_deadline_at": 0, "error": "", "error_code": "", "recovery": ""}
        try:
            updated = self.store.transition(operation["pk"], operation["phase"], changes)
        except Exception:
            self.store.secret_delete(ref)
            raise
        self.store.secret_delete(operation.get("attempt_ref"))
        self.store.secret_delete(operation.get("callback_ref"))
        operation = updated
        self.store.put({"pk": "STATE#" + digest(state), "operation_id": operation["id"], "expires_at": now + 1800}, absent=True)
        client = self.store.secret_get(operation["client_ref"])
        result = public_operation(operation)
        result["authorizationUrl"] = self.google.authorization_url(client, self.callback_url, state, verifier, nonce, operation["email"])
        return result

    def operation(self, owner, operation_id):
        return public_operation(self._owned(owner, operation_id))

    def _owned(self, owner, operation_id):
        op = self.store.get("OP#" + str(operation_id))
        if not op or op.get("owner") != owner:
            raise WorkflowError(404, "not_found", "Operation not found.")
        return op

    def attempts(self, owner, operation_id, session_id):
        op = self._owned(owner, operation_id)
        if op["phase"] not in ("authorizing", "failed", "exchanging", "ready", "verification_failed"):
            raise WorkflowError(409, "consent_not_needed", "Check the saved operation status.")
        if op["phase"] == "exchanging" and int(time.time()) < op.get("consent_expires_at", 0):
            raise WorkflowError(409, "exchange_in_progress", "Wait for this consent attempt to finish.")
        if op["phase"] != "authorizing" or int(time.time()) >= op.get("consent_expires_at", 0):
            self.store.lock_account(op["email"], op["id"], int(time.time()))
        return self._consent(op, session_id)

    def callback(self, state, code, session_id, error=None):
        state_row = self.store.get("STATE#" + digest(str(state)))
        op = self.store.get("OP#" + state_row["operation_id"]) if state_row else None
        if not op or op.get("state_hash") != digest(str(state)) or op.get("session_hash") != digest(session_id):
            raise WorkflowError(400, "invalid_state", "This consent callback does not match the owner session.")
        if int(time.time()) >= op["consent_expires_at"]:
            raise WorkflowError(400, "consent_expired", "Start a fresh consent attempt.")
        op = self.store.transition(op["pk"], "authorizing", {"phase": "exchanging", "status": "running",
            "exchange_deadline_at": int(time.time()) + 120}, conditions={"state_hash": digest(str(state)), "session_hash": digest(session_id)})
        saved = False
        try:
            if error or not code:
                raise WorkflowError(400, "consent_denied", "Google consent was not completed; start a fresh attempt.")
            client = self.store.secret_get(op["client_ref"])
            attempt = self.store.secret_get(op["attempt_ref"])
            tokens = self.google.exchange(client, self.callback_url, code, attempt["verifier"])
            reference = self.store.secret_put("callbacks/" + op["id"] + "/" + op["state_hash"], {"tokens": tokens, "nonce": attempt["nonce"]})
            saved = True
            op = self.store.transition(op["pk"], "exchanging", {"phase": "validating", "status": "running",
                "callback_ref": reference, "exchange_saved": True, "updated_at": int(time.time())})
            return public_operation(op)
        except Exception as exc:
            if saved:
                # The stream's exchanging deadline reconciles a crash between SSM and DynamoDB.
                raise WorkflowError(503, "authorization_saved", "Authorization is saved; reload to check background validation.") from None
            code = exc.code if isinstance(exc, WorkflowError) else "consent_exchange_interrupted"
            self.store.transition(op["pk"], "exchanging", {"phase": "failed", "status": "failed", "error": code,
                "recovery": "Start a fresh consent attempt; existing mailbox access is preserved."})
            self.store.release_account(op["email"], op["id"])
            self.store.secret_delete(op.get("attempt_ref"))
            if isinstance(exc, WorkflowError):
                raise
            raise WorkflowError(503, code, "The consent exchange was interrupted; start a fresh attempt.") from None

    def validate_candidate_operation(self, op):
        """Resume only saved tokens; the worker holds the conditional operation lease."""
        client = self.store.secret_get(op["client_ref"])
        exchange = self.store.secret_get(op["callback_ref"])
        tokens = exchange["tokens"]
        account = self.store.get("ACCOUNT#" + op["email"]) or {}
        subject = exchange.get("verified_subject")
        if not subject:
            subject = self.google.identity(tokens, client, exchange["nonce"], op["email"], account.get("google_subject"))
            exchange["verified_subject"] = subject
            suffix = op["callback_ref"].removeprefix(self.store.prefix + "/onboarding/")
            self.store.secret_put(suffix, exchange)
        refresh = tokens.get("refresh_token")
        if not refresh and account.get("google_subject") == subject:
            old = self.store.secret_get(account_parameter(self.store.prefix, op["email"]))
            refresh = old.get("refresh_token") if old.get("client_id") == client["client_id"] else None
        if not refresh:
            raise WorkflowError(400, "refresh_required", "Google did not provide durable access; start a fresh consent attempt.")
        credentials = {"type": "oauth_refresh", "client_id": client["client_id"],
            "client_secret": client["client_secret"], "refresh_token": refresh, "scopes": SCOPES}
        self.google.validate_candidate(op["email"], credentials)
        candidate = self.store.secret_put("candidates/" + op["id"], credentials)
        return {"phase": "pending_promotion", "status": "running", "candidate_ref": candidate,
            "google_subject": subject, "validation_complete": True, "expires_at": int(time.time()) + 604800}

    def retry(self, owner, operation_id):
        op = self._owned(owner, operation_id)
        if op["phase"] != "verification_failed":
            raise WorkflowError(409, "retry_not_available", "Only saved authorization awaiting verification can be retried.")
        self.store.lock_account(op["email"], op["id"], int(time.time()))
        phase = "verifying" if op.get("promoted_at") else "pending_promotion"
        if not op.get("validation_complete") and op.get("exchange_deadline_at"):
            phase = "validating" if op.get("exchange_saved") else "exchanging"
        op = self.store.transition(op["pk"], "verification_failed", {"phase": phase, "status": "running", "updated_at": int(time.time()), "attempts": 0, "next_attempt": int(time.time()), "error_code": "", "error": ""})
        return public_operation(op)


def build_service():
    import boto3
    import requests
    table = boto3.resource("dynamodb").Table(os.environ["ONBOARDING_TABLE"])
    store = Store(table, boto3.client("ssm"), os.environ.get("GOG_SSM_PREFIX", "/gogcli-proxy"))
    return OnboardingService(store, GoogleClient(requests.Session()), os.environ["GOOGLE_CALLBACK_URL"])
