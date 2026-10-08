"""Persisted enrollment/removal execution and named, read-only service evidence."""
import json
import math
import os
import time
from urllib.parse import urlencode

import requests
from onboarding_effects import apply_access, promote, remove_connection
from onboarding_google import check_response
from onboarding_services import probes, validate_probe
from errors import WorkflowError
from onboarding_store import new_id
from onboarding_consent import prepare_recovery, validate_candidate
import onboarding_runtime as runtime

ACTIVE = {"awaiting_connection", "preparing_consent", "authorizing", "exchanging", "validating", "preparing_recovery",
          "checking_candidate", "pending_promotion", "verifying", "applying_access", "removing", "propagating"}
TERMINAL = {"ready", "failed", "canceled", "disconnected"}


def aws_client(service):
    import boto3
    from botocore.config import Config
    return boto3.client(service, config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}))


def signed_headers(url, email, host, api_key):
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    request = AWSRequest(method="GET", url=url, headers={"X-GOG-Account": email, "X-GOG-Google-Host": host, "X-API-Key": api_key})
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, "execute-api", os.environ["AWS_REGION"]).add_auth(request)
    return dict(request.headers.items())


def verify_proxy(email, http, services):
    base = os.environ["PROXY_BASE_URL"].rstrip("/")
    key = aws_client("apigateway").get_api_key(apiKey=os.environ["PROXY_API_KEY_ID"], includeValue=True)["value"]
    evidence = {}
    for name in services:
        try:
            for index, (host, path, query) in enumerate(probes(name)):
                url = base + path + ("?" + urlencode(query) if query else "")
                response = http.get(url, headers=signed_headers(url, email, host, key), timeout=(2, 3))
                check_response(response, proxy=True)
                validate_probe(name, index, response.json(), email)
        except WorkflowError as error:
            evidence[name] = {"status": "failed", "error_code": error.code}
        except (requests.RequestException, ValueError, TypeError):
            evidence[name] = {"status": "failed", "error_code": "provider_unavailable"}
        else:
            evidence[name] = {"status": "succeeded"}
    return evidence


def claim(store, operation, now):
    lease = new_id("ls")
    try:
        claimed = store.claim_operation(operation, lease, now)
        return claimed, lease
    except WorkflowError:
        return None, None


def finish(store, operation, lease, changes):
    return store.finish_operation(operation, lease, changes)


def process(operation_id, store=None, http=None, now=None):
    store, http, now = store or runtime.runtime_store(), http or requests.Session(), int(time.time() if now is None else now)
    op = store.get("OP#" + operation_id)
    if not op or op["phase"] not in ACTIVE:
        return
    due = int(op.get("next_attempt", 0))
    if now < due:
        enqueue(operation_id, due - now)
        return
    if op.get("lease_until", 0) > now:
        enqueue(operation_id, op["lease_until"] - now + 1)
        return
    op, lease = claim(store, op, now)
    if not op:
        enqueue(operation_id, 65)
        return
    account = store.get("ACCOUNT#" + op["email"]) or {}
    if account.get("active_operation") != op["id"] or account.get("generation") != op["generation"]:
        try:
            store.terminal(op, {"phase": "canceled", "status": "canceled", "error_code": "operation_superseded"}, now)
        except WorkflowError:
            pass
        return
    started = time.monotonic()
    changes = {"updated_at": now, "status": "running"}
    try:
        phase = op["phase"]
        if phase == "awaiting_connection":
            changes.update(phase="failed", status="failed", error_code="connection_link_expired",
                recovery="Ask the owner for a fresh connection link.")
        elif phase in {"preparing_consent", "authorizing"}:
            changes.update(phase="failed", status="failed", error_code="consent_expired",
                recovery="Start fresh consent; existing installed access is preserved.")
        elif phase == "exchanging":
            if store.optional_secret(op["callback_ref"]):
                changes.update(phase="validating", exchange_saved=True, next_attempt=now)
            else:
                changes.update(phase="failed", status="failed", error_code="consent_exchange_interrupted",
                    recovery="An unsaved Google exchange cannot be repeated safely; restart consent.")
        elif phase == "preparing_recovery":
            changes.update(prepare_recovery(store, op, now))
        elif phase == "validating":
            from onboarding_google import GoogleClient
            changes.update(validate_candidate(store, GoogleClient(http), op), next_attempt=now, attempts=0)
        elif phase == "checking_candidate":
            from onboarding_google import GoogleClient
            candidate = store.secret_get(op["candidate_ref"])
            GoogleClient(http).validate_candidate(op["email"], candidate, op["services"])
            changes.update(phase="pending_promotion", validation_complete=True, next_attempt=now)
        elif phase == "pending_promotion":
            changes.update(promote(store, op, now))
        elif phase == "verifying":
            changes.update(check_installed(store, op, http, now))
        elif phase == "applying_access":
            changes.update(apply_access(store, op, now))
        elif phase == "removing":
            changes.update(remove_connection(store, op, now))
        elif phase == "propagating":
            if op["kind"] == "disconnect":
                store.update_account(op, {"connection_status": "disconnected"})
            changes.update(phase="disconnected" if op["kind"] == "disconnect" else "ready", status="succeeded")
    except WorkflowError as error:
        if error.code == "operation_superseded":
            changes.update(phase="canceled", status="canceled", error_code=error.code)
        elif error.status == 409 and error.code == "operation_conflict":
            changes.update(next_attempt=now + 65)
        elif error.code in {"identity_mismatch", "invalid_identity", "missing_service_permission", "refresh_required", "invalid_candidate",
                            "operator_policy_requires_review", "broad_allowlist_requires_operator", "consent_required"}:
            changes.update(phase="failed", status="failed", error_code=error.code, recovery=error.detail)
        else:
            retry_failure(op, changes, now, error.code)
    except Exception:
        retry_failure(op, changes, now, "provider_unavailable")
    if changes.get("phase") in TERMINAL:
        preserve_recovery(store, op, changes, now)
        try:
            store.terminal(op, changes, now)
        except WorkflowError:
            return  # New intent or another terminal writer won the conditional fence.
    else:
        try:
            finish(store, op, lease, changes)
        except WorkflowError:
            return
    print(json.dumps({"event": "onboarding_operation", "operation_id": operation_id, "kind": op["kind"],
        "phase": changes.get("phase", op["phase"]), "status": changes["status"], "error_code": changes.get("error_code", ""),
        "duration_ms": round((time.monotonic() - started) * 1000)}))


def check_installed(store, op, http, now):
    evidence = verify_proxy(op["email"], http, op["services"])
    current = store.get("ACCOUNT#" + op["email"]) or {}
    if current.get("credential_revision") != op["credential_revision"]:
        raise WorkflowError(409, "operation_superseded", "The installed credential revision changed during its check.")
    for value in evidence.values():
        value.update(verified_at=now, credential_revision=op["credential_revision"], principal=os.environ["ONBOARDING_VERIFIER_ROLE_ARN"])
    changes = {"verifications": evidence}
    errors = [value["error_code"] for value in evidence.values() if value["status"] == "failed"]
    if not errors:
        changes["connection_status"] = "connected"
    elif any(code in {"authorization_unusable", "refresh_revoked"} for code in errors):
        changes["connection_status"] = "needs_attention"
    store.update_account(op, changes)
    if errors:
        return {"phase": "failed", "status": "failed", "error_code": errors[0],
            "recovery": "Saved connection retained; retry its check or resolve the named service permission."}
    return {"phase": "ready", "status": "succeeded", "verified_at": now, "error_code": ""}


def preserve_recovery(store, op, changes, now):
    if changes["phase"] != "failed" or op["kind"] not in {"enrollment", "recovery"} or op.get("promoted_at"):
        return
    if changes.get("error_code") in {"identity_mismatch", "invalid_identity", "missing_service_permission", "refresh_required",
                                     "refresh_revoked", "invalid_candidate", "consent_expired", "consent_exchange_interrupted"}:
        return
    latest = store.get(op["pk"]) or op
    refs = {key: latest[key] for key in ("callback_ref", "candidate_ref", "client_ref", "client_revision", "project_id", "services", "google_subject", "granted_scopes", "policy_write_hash") if key in latest}
    for field in ("callback_ref", "candidate_ref"):
        if refs.get(field) and store.optional_secret(refs[field]) is None:
            refs.pop(field)
    if refs.get("callback_ref") or refs.get("candidate_ref"):
        try:
            store.update_account(op, {"saved_recovery": {**refs, "expires_at": now + 604800}})
        except WorkflowError:
            pass


def retry_failure(operation, changes, now, code):
    attempts = int(operation.get("attempts", 0)) + 1
    if attempts < 5:
        changes.update(attempts=attempts, next_attempt=now + min(900, 30 * 2 ** attempts), error_code=code)
    else:
        changes.update(phase="failed", status="failed", error_code=code, recovery="Retry saved account authorization or resolve the reported setup issue.")


def enqueue(operation_id, delay=0):
    aws_client("sqs").send_message(QueueUrl=os.environ["ONBOARDING_QUEUE_URL"], MessageBody=operation_id,
        DelaySeconds=min(900, max(0, math.ceil(delay))))


def cleanup(store, item):
    prefix = store.prefix + "/onboarding/"
    for field, directory in (("candidate_ref", "candidates/"), ("callback_ref", "callbacks/"), ("attempt_ref", "attempts/"), ("connection_link_ref", "attempts/"), ("secret_ref", "owner-logins/")):
        reference = item.get(field, "")
        if reference.startswith(prefix + directory):
            store.secret_delete(reference)


def handler(event, context):
    from boto3.dynamodb.types import TypeDeserializer
    deserialize, failures, store = TypeDeserializer(), [], None
    for record in event.get("Records", []):
        try:
            if record.get("eventSource") == "aws:dynamodb":
                document = record.get("dynamodb", {})
                item = {key: deserialize.deserialize(value) for key, value in document.get("NewImage", {}).items()}
                old = {key: deserialize.deserialize(value) for key, value in document.get("OldImage", {}).items()}
                if record.get("eventName") == "REMOVE":
                    store = store or runtime.runtime_store()
                    cleanup(store, old)
                elif item.get("pk", "").startswith("OP#") and item.get("phase") in ACTIVE:
                    if item.get("phase") != old.get("phase") or item.get("next_attempt") != old.get("next_attempt"):
                        enqueue(item["id"], int(item.get("next_attempt", 0)) - time.time())
            else:
                operation_id = record["body"]
                if not operation_id.startswith("op_") or len(operation_id) != 25:
                    raise ValueError("invalid operation id")
                store = store or runtime.runtime_store()
                if record.get("eventSourceARN") == os.getenv("ONBOARDING_DLQ_ARN"):
                    op = store.get("OP#" + operation_id)
                    if op and op["phase"] in ACTIVE:
                        now = int(time.time())
                        account = store.get("ACCOUNT#" + op["email"]) or {}
                        if max(int(op.get("next_attempt", 0)), int(op.get("lease_until", 0)),
                               int(account.get("effect_lease_until", 0))) > now:
                            raise WorkflowError(409, "delivery_still_active", "Account execution is still active; retry delivery later.")
                        claimed, _ = claim(store, op, now)
                        if claimed is None:
                            raise WorkflowError(409, "delivery_still_active", "Another execution claimed the operation; retry delivery later.")
                        preserve_recovery(store, claimed, {"phase": "failed"}, now)
                        store.terminal(claimed, {"phase": "failed", "status": "failed", "error_code": "worker_delivery_failed",
                            "recovery": "Retry the saved account action."}, now)
                else:
                    process(operation_id, store=store)
        except Exception as exc:
            print(json.dumps({"event": "onboarding_delivery_error", "source": record.get("eventSource", "unknown"), "error_type": type(exc).__name__}))
            failures.append({"itemIdentifier": record.get("messageId") or record.get("dynamodb", {}).get("SequenceNumber") or record["eventID"]})
    return {"batchItemFailures": failures}
