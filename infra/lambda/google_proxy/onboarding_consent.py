"""One-shot OAuth exchanges and recovery from already encrypted responses."""
import secrets
import time

from onboarding_services import public_operation, requested_scopes
from onboarding_store import WorkflowError, account_parameter, digest, new_id


def consent_response(service, op, session_id):
    if op.get("session_hash") != digest(session_id) or op.get("consent_expires_at", 0) <= int(time.time()):
        raise WorkflowError(409, "consent_restart_required", "Restart Google consent in this owner session.")
    attempt = service.store.secret_get(op["attempt_ref"])
    return {**public_operation(op), "authorizationUrl": attempt["authorization_url"]}


def start_consent(service, op, session_id):
    now = int(time.time())
    state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    client = service.store.secret_get(op["client_ref"])
    url = service.google.authorization_url(client, service.callback_url, state, verifier, nonce, op["email"], op["services"])
    ref = service.store.secret_put("attempts/" + op["id"] + "/" + new_id("at"),
        {"state": state, "verifier": verifier, "nonce": nonce, "authorization_url": url})
    try:
        # Reserve state before publishing authorizing; crash recovery starts fresh consent.
        service.store.put({"pk": "STATE#" + digest(state), "operation_id": op["id"], "expires_at": now + 1800}, absent=True)
        account = service.account(op["owner"], op["email"])
        if account.get("generation") != op["generation"] or account.get("active_operation") != op["id"]:
            raise WorkflowError(409, "operation_superseded", "This consent attempt was superseded by another account action.")
        current = service.store.transition(op["pk"], "preparing_consent", {"phase": "authorizing", "status": "accepted",
            "state_hash": digest(state), "attempt_ref": ref, "callback_ref": service.store.prefix + "/onboarding/callbacks/" + op["id"],
            "consent_expires_at": now + 1800, "next_attempt": now + 1800})
    except Exception:
        service.store.secret_delete(ref)
        raise
    return {**public_operation(current), "authorizationUrl": url}


def callback(service, state, code, session_id, error):
    state_row = service.store.get("STATE#" + digest(str(state)))
    op = service.store.get("OP#" + state_row["operation_id"]) if state_row else None
    if not op or op.get("state_hash") != digest(str(state)) or op.get("session_hash") != digest(session_id):
        raise WorkflowError(400, "invalid_state", "This consent callback does not match the owner session.")
    account = service.account(op["owner"], op["email"])
    connection = op.get("auth_mode") == "connection"
    if connection and op.get("connection_session_expires_at", 0) <= int(time.time()):
        raise WorkflowError(400, "invalid_state", "This connection session expired.")
    if account.get("generation") != op["generation"] or (account.get("active_operation") != op["id"] and not (connection and op["phase"] in {"ready", "failed", "canceled"})):
        raise WorkflowError(400, "invalid_state", "This consent attempt has been superseded.")
    if connection and op["phase"] != "authorizing":
        return public_operation(op)
    if int(time.time()) >= op["consent_expires_at"]:
        raise WorkflowError(400, "consent_expired", "Start a fresh consent attempt.")
    try:
        op = service.store.transition(op["pk"], "authorizing", {"phase": "exchanging", "status": "running",
            "next_attempt": int(time.time()) + 120}, conditions={"state_hash": digest(str(state)), "session_hash": digest(session_id)})
    except WorkflowError:
        if connection:
            current = service.store.get(op["pk"])
            if current and current["phase"] != "authorizing":
                return callback(service, state, code, session_id, error)
        raise
    saved = False
    try:
        if error or not code:
            raise WorkflowError(400, "consent_denied", "Google consent was declined; start a fresh attempt.")
        client = service.store.secret_get(op["client_ref"])
        attempt = service.store.secret_get(op["attempt_ref"])
        tokens = service.google.exchange(client, service.callback_url, code, attempt["verifier"])
        service.store.secret_put("callbacks/" + op["id"], {"tokens": tokens, "nonce": attempt["nonce"]})
        saved = True
        op = service.store.transition(op["pk"], "exchanging", {"phase": "validating", "next_attempt": int(time.time()),
            "exchange_saved": True, "updated_at": int(time.time())})
        return public_operation(op)
    except Exception as exc:
        if saved:
            raise WorkflowError(503, "authorization_saved", "Authorization is saved; reload background validation status.") from None
        error_code = exc.code if isinstance(exc, WorkflowError) else "consent_exchange_interrupted"
        try:
            service.store.terminal(op, {"phase": "failed", "status": "failed", "error_code": error_code,
                "recovery": "Start fresh consent; existing installed access is preserved."}, int(time.time()))
        except WorkflowError:
            pass  # A newer restrictive command already fenced this callback.
        raise WorkflowError(400, error_code, "Google consent did not complete; start a fresh attempt.") from None


def validate_candidate(service, op):
    client = service.store.secret_get(op["client_ref"])
    exchange = service.store.secret_get(op["callback_ref"])
    tokens = exchange["tokens"]
    account = service.store.get("ACCOUNT#" + op["email"]) or {}
    identity = exchange.get("verified_identity")
    if not identity:
        identity = service.google.identity(tokens, client, exchange["nonce"], op["email"], op["services"], account.get("google_subject"))
        exchange["verified_identity"] = identity
        service.store.secret_put(op["callback_ref"].removeprefix(service.store.prefix + "/onboarding/"), exchange)
    refresh = tokens.get("refresh_token")
    if not refresh and account.get("google_subject") == identity["subject"]:
        old = service.store.optional_secret(account_parameter(service.store.prefix, op["email"]))
        if old and old.get("client_id") == client["client_id"]:
            refresh = old.get("refresh_token")
    if not refresh:
        raise WorkflowError(400, "refresh_required", "Google did not supply durable authorization; restart consent.")
    credentials = {"type": "oauth_refresh", "client_id": client["client_id"], "client_secret": client["client_secret"],
        "refresh_token": refresh, "scopes": requested_scopes(op["services"]), "credential_revision": op["id"]}
    candidate = service.store.secret_put("candidates/" + op["id"], credentials)
    return {"phase": "checking_candidate", "candidate_ref": candidate, "google_subject": identity["subject"],
        "granted_scopes": identity["granted_scopes"], "identity_complete": True, "credential_revision": op["id"]}
