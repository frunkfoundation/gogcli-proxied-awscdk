"""Durable candidate promotion and delayed, read-only proxy verification."""
import base64
import json
import math
import os
import time
import uuid
from urllib.parse import urlencode

import requests
from onboarding_store import Store, WorkflowError, account_parameter

CLI_CALLER = "arn:aws:iam::968138089939:role/JayDeveloperFromJdev"
ACTIVE = {"exchanging", "validating", "pending_promotion", "verifying"}


class PermissionBlocked(Exception):
    pass


class IdentityMismatch(Exception):
    pass


class TransientFailure(Exception):
    pass


def aws_client(service):
    import boto3
    return boto3.client(service)


def aws_credentials():
    import boto3
    return boto3.Session().get_credentials()


def runtime_store():
    import boto3
    table = boto3.resource("dynamodb").Table(os.environ["ONBOARDING_TABLE"])
    return Store(table, aws_client("ssm"), os.environ["GOG_SSM_PREFIX"])


def signed_headers(url, email):
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    key = aws_client("apigateway").get_api_key(apiKey=os.environ["PROXY_API_KEY_ID"], includeValue=True)["value"]
    request = AWSRequest(method="GET", url=url, headers={"X-GOG-Account": email, "X-GOG-Google-Host": "gmail.googleapis.com", "X-API-Key": key})
    SigV4Auth(aws_credentials().get_frozen_credentials(), "execute-api", os.environ["AWS_REGION"]).add_auth(request)
    return dict(request.headers.items())


def verify_proxy(email, http):
    base = os.environ["PROXY_BASE_URL"].rstrip("/")
    paths = ["/gmail/v1/users/me/profile", "/gmail/v1/users/me/messages?" + urlencode({"maxResults": 1, "fields": "messages/id,resultSizeEstimate"})]
    for index, path in enumerate(paths):
        url = base + path
        response = http.get(url, headers=signed_headers(url, email), timeout=(3, 10))
        if response.status_code in {401, 403}:
            raise PermissionBlocked()
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientFailure()
        if response.status_code != 200:
            raise IdentityMismatch()
        payload = response.json()
        if index == 0 and str(payload.get("emailAddress", "")).lower() != email:
            raise IdentityMismatch()
        if index == 1 and not isinstance(payload.get("messages", []), list):
            raise IdentityMismatch()


def extend_allowlists(store, email):
    for caller in (CLI_CALLER, os.environ["ONBOARDING_VERIFIER_ROLE_ARN"]):
        encoded = base64.urlsafe_b64encode(caller.encode()).decode().rstrip("=")
        name, key, lease, now = store.prefix + "/allow/" + encoded, "ALLOWLIST#" + encoded, uuid.uuid4().hex, int(time.time())
        # SSM has no compare-and-swap. Serialize each caller's email grants in DynamoDB.
        store.table.update_item(Key={"pk": key},
            ConditionExpression="attribute_not_exists(lease_until) OR lease_until <= :now",
            UpdateExpression="SET lease_until = :until, lease_owner = :owner, expires_at = :expires",
            ExpressionAttributeValues={":now": now, ":until": now + 60, ":owner": lease, ":expires": now + 300})
        try:
            try:
                current = store.secret_get(name)
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ParameterNotFound":
                    raise
                current = {}
            # Existing explicit email grants are retained; domain and wildcard grants are obsolete.
            emails = {str(value).strip().lower() for value in current.get("emails", []) if value != "*"}
            emails.add(email)
            store.ssm.put_parameter(Name=name, Type="SecureString", KeyId="alias/aws/ssm",
                Value=json.dumps({"emails": sorted(emails)}), Overwrite=True)
        finally:
            store.table.update_item(Key={"pk": key}, ConditionExpression="lease_owner = :owner",
                UpdateExpression="REMOVE lease_until, lease_owner", ExpressionAttributeValues={":owner": lease})


def claim(store, operation, now):
    lease = uuid.uuid4().hex
    try:
        result = store.table.update_item(Key={"pk": operation["pk"]},
            ConditionExpression="#phase = :phase AND (attribute_not_exists(lease_until) OR lease_until <= :now)",
            UpdateExpression="SET lease_until = :until, lease_owner = :owner",
            ExpressionAttributeNames={"#phase": "phase"}, ExpressionAttributeValues={
                ":phase": operation["phase"], ":now": now, ":until": now + 60, ":owner": lease}, ReturnValues="ALL_NEW")
        return result["Attributes"], lease
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return None, None
        raise


def finish(store, operation, lease, changes):
    names, values, updates = {"#phase": "phase"}, {":phase": operation["phase"], ":owner": lease}, []
    for index, (name, value) in enumerate(changes.items()):
        names[f"#n{index}"] = name
        values[f":v{index}"] = value
        updates.append(f"#n{index} = :v{index}")
    store.table.update_item(Key={"pk": operation["pk"]}, ConditionExpression="#phase = :phase AND lease_owner = :owner",
        UpdateExpression="SET " + ", ".join(updates) + " REMOVE lease_until, lease_owner",
        ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def process(operation_id, store=None, http=None, now=None):
    store, http, now = store or runtime_store(), http or requests.Session(), int(time.time() if now is None else now)
    operation = store.get("OP#" + operation_id)
    if not operation or operation["phase"] not in ACTIVE:
        return
    due = max(int(operation.get("next_attempt", 0)), int(operation.get("exchange_deadline_at", 0)) if operation["phase"] == "exchanging" else 0,
        int(operation.get("promoted_at", 0)) + 65 if operation.get("promoted_at") else 0)
    if now < due:
        enqueue(operation_id, due - now)
        return
    operation, lease = claim(store, operation, now)
    if operation is None:
        # Keep delivery alive when another execution died while holding its lease.
        enqueue(operation_id, 65)
        return
    started = time.monotonic()
    changes = {"updated_at": now}
    try:
        if operation["phase"] == "exchanging":
            try:
                store.secret_get(operation["callback_ref"])
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ParameterNotFound":
                    raise
                changes.update(phase="failed", status="failed", error_code="consent_exchange_interrupted", error="consent_exchange_interrupted",
                    recovery="The interrupted Google exchange cannot be repeated safely. Start a fresh consent attempt.")
            else:
                changes.update(phase="validating", status="running", exchange_saved=True, next_attempt=now, attempts=0)
        elif operation["phase"] == "validating":
            from onboarding_google import GoogleClient
            from onboarding_operations import OnboardingService
            service = OnboardingService(store, GoogleClient(http), os.environ["GOOGLE_CALLBACK_URL"])
            changes.update(service.validate_candidate_operation(operation))
            changes.update(attempts=0, next_attempt=now)
        elif operation["phase"] == "pending_promotion":
            account = store.get("ACCOUNT#" + operation["email"]) or {}
            if account.get("active_operation") != operation_id:
                raise IdentityMismatch()
            candidate = store.secret_get(operation["candidate_ref"])
            if candidate.get("type") != "oauth_refresh" or not all(candidate.get(field) for field in ("client_id", "client_secret", "refresh_token")):
                raise IdentityMismatch()
            store.ssm.put_parameter(Name=account_parameter(store.prefix, operation["email"]), Type="SecureString",
                KeyId="alias/aws/ssm", Value=json.dumps(candidate), Overwrite=True)
            extend_allowlists(store, operation["email"])
            store.table.update_item(Key={"pk": "ACCOUNT#" + operation["email"]},
                ConditionExpression="active_operation = :op",
                UpdateExpression="SET operation_id = :op, google_subject = :subject, client_revision = :revision",
                ExpressionAttributeValues={":op": operation_id, ":subject": operation["google_subject"], ":revision": operation["client_revision"]})
            changes.update(phase="verifying", status="running", promoted_at=now, next_attempt=now + 65, attempts=0)
        else:
            verify_proxy(operation["email"], http)
            changes.update(phase="ready", status="succeeded", verified_at=now, recovery="none", error_code="")
    except PermissionBlocked:
        changes.update(phase="verification_failed", status="failed", error_code="permission_blocked",
            recovery="Grant the CLI and verifier access to this API, then retry verification. Saved Google authorization is retained.")
    except WorkflowError as exc:
        if exc.code in {"identity_mismatch", "refresh_required"}:
            changes.update(phase="failed", status="failed", error_code=exc.code, error=exc.code,
                recovery="Start a fresh Google consent attempt; existing installed credentials are preserved.")
        else:
            retry_failure(operation, changes, now)
    except IdentityMismatch:
        changes.update(phase="verification_failed", status="failed", error_code="identity_mismatch", recovery="Review the account and reconnect it.")
    except Exception:
        retry_failure(operation, changes, now)
    finish(store, operation, lease, changes)
    if changes.get("phase") in {"pending_promotion", "failed"}:
        store.secret_delete(operation.get("callback_ref"))
        store.secret_delete(operation.get("attempt_ref"))
    if changes.get("phase") in {"ready", "verification_failed", "failed"}:
        store.release_account(operation["email"], operation_id)
    print(json.dumps({"event": "onboarding_operation", "operation_id": operation_id,
        "phase": changes.get("phase", operation["phase"]), "status": changes.get("status", operation["status"]),
        "error_code": changes.get("error_code", ""), "attempt": changes.get("attempts", operation.get("attempts", 0)),
        "duration_ms": round((time.monotonic() - started) * 1000)}))


def retry_failure(operation, changes, now):
    attempts = int(operation.get("attempts", 0)) + 1
    if attempts < 5:
        changes.update(attempts=attempts, next_attempt=now + min(900, 30 * 2 ** attempts))
    else:
        changes.update(phase="verification_failed", status="failed", error_code="retry_exhausted",
            recovery="Retry saved authorization; Google consent does not need to be repeated.")


def enqueue(operation_id, delay=0):
    aws_client("sqs").send_message(QueueUrl=os.environ["ONBOARDING_QUEUE_URL"], MessageBody=operation_id,
        DelaySeconds=min(900, max(0, math.ceil(delay))))


def cleanup(store, item):
    # TTL records may reference temporary secrets, never installed account/client parameters.
    prefix = store.prefix + "/onboarding/"
    for field, directory in (("candidate_ref", "candidates/"), ("callback_ref", "callbacks/"), ("attempt_ref", "attempts/"), ("secret_ref", "owner-logins/")):
        reference = item.get(field, "")
        if reference.startswith(prefix + directory):
            store.secret_delete(reference)


def handler(event, context):
    from boto3.dynamodb.types import TypeDeserializer
    deserialize = TypeDeserializer()
    failures = []
    store = None
    for record in event.get("Records", []):
        try:
            if record.get("eventSource") == "aws:dynamodb":
                document = record.get("dynamodb", {})
                item = {key: deserialize.deserialize(value) for key, value in document.get("NewImage", {}).items()}
                old = {key: deserialize.deserialize(value) for key, value in document.get("OldImage", {}).items()}
                if record.get("eventName") == "REMOVE":
                    store = store or runtime_store()
                    cleanup(store, old)
                elif item.get("pk", "").startswith("OP#") and item.get("phase") in ACTIVE:
                    if item.get("phase") != old.get("phase") or item.get("next_attempt") != old.get("next_attempt"):
                        enqueue(item["id"], int(item.get("next_attempt", 0)) - time.time())
            else:
                operation_id = record["body"]
                if not operation_id.startswith("op_") or len(operation_id) != 25:
                    raise ValueError("invalid operation id")
                store = store or runtime_store()
                if record.get("eventSourceARN") == os.getenv("ONBOARDING_DLQ_ARN"):
                    operation = store.get("OP#" + operation_id)
                    if operation and operation["phase"] in ACTIVE:
                        store.transition(operation["pk"], operation["phase"], {"phase": "verification_failed", "status": "failed",
                            "error_code": "worker_delivery_failed", "recovery": "Retry verification; saved authorization is retained."})
                        store.release_account(operation["email"], operation_id)
                else:
                    process(operation_id, store=store)
        except Exception as exc:
            print(json.dumps({"event": "onboarding_delivery_error", "source": record.get("eventSource", "unknown"), "error_type": type(exc).__name__}))
            identifier = record.get("messageId") or record.get("dynamodb", {}).get("SequenceNumber") or record["eventID"]
            failures.append({"itemIdentifier": identifier})
    return {"batchItemFailures": failures}
