"""Caller permissions and policy decisions for prepared proxy requests."""
import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Mapping

import boto3
import proxy_config
from errors import ProxyError
from policy import _evaluate_policy, _compose_policy_documents, _normalize_policy_document
from proxy_http import _normalize_email

_EVENTS = boto3.client("events")


def _log_event(event_type: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {"event": event_type}
    payload.update(fields)
    try:
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    except Exception:
        # Last-resort log safety to avoid breaking request flow.
        print(f'{{"event":"{event_type}","log_error":"serialization_failed"}}')


def _b64url_nopad(raw: str) -> str:
    b = raw.encode("utf-8")
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _caller_id(event: Mapping[str, Any]) -> str:
    rc = event.get("requestContext") or {}
    ident = rc.get("identity") if isinstance(rc, dict) else None
    if isinstance(ident, dict):
        cid = str(ident.get("cognitoIdentityId") or "").strip()
        if cid:
            return cid
        arn = str(ident.get("userArn") or ident.get("caller") or "").strip()
        assumed = re.fullmatch(r"arn:([a-z0-9-]+):sts::([0-9]{12}):assumed-role/(.+)/([^/]+)", arn)
        if assumed:
            partition, account, role, _ = assumed.groups()
            return f"arn:{partition}:iam::{account}:role/{role}"
        if re.fullmatch(r"arn:[a-z0-9-]+:iam::[0-9]{12}:(?:user|role)/[^\s]+", arn):
            return arn
    raise ProxyError(403, "missing or invalid authenticated caller identity")


def _validate_allowlist(allow: Mapping[str, Any]) -> None:
    for key in ("emails", "domains"):
        values = allow.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ProxyError(500, "invalid caller account permission")


def _is_allowed(allow: Mapping[str, Any], email: str) -> bool:
    _validate_allowlist(allow)
    email = _normalize_email(email)
    if "*" in [str(x).strip() for x in (allow.get("emails") or []) if x is not None]:
        return True
    if "*" in [str(x).strip() for x in (allow.get("domains") or []) if x is not None]:
        return True

    allowed_emails = {_normalize_email(str(x)) for x in (allow.get("emails") or []) if x is not None}
    if email in allowed_emails:
        return True

    if "@" in email:
        domain = email.split("@", 1)[1]
        allowed_domains = {str(x).strip().lower() for x in (allow.get("domains") or []) if x is not None}
        if domain in allowed_domains:
            return True

    return False


def _load_effective_policy(policy_prefix: str, caller_id: str, account_email: str) -> Dict[str, Any]:
    entries = [
        ("default", 0, f"{policy_prefix}/default"),
        ("caller", 1, f"{policy_prefix}/caller/{_b64url_nopad(caller_id)}"),
        ("account", 2, f"{policy_prefix}/account/{_b64url_nopad(_normalize_email(account_email))}"),
    ]
    docs: List[Dict[str, Any]] = []
    for source, rank, name in entries:
        raw = proxy_config._ssm_get_policy_json(name)
        if raw is None:
            continue
        docs.append(_normalize_policy_document(raw, source=source, source_rank=rank))
    if not any(doc["source"] == "default" for doc in docs):
        raise ProxyError(403, "policy_not_found")
    return _compose_policy_documents(docs)


def _maybe_emit_policy_event(decision: Mapping[str, Any], ctx: Mapping[str, Any], status_code: int) -> None:
    if not proxy_config._policy_events_enabled():
        return
    on_match = decision.get("on_match") or {}
    if not bool(on_match.get("emit_event")):
        return

    bus_arn = (os.getenv("GOG_POLICY_EVENT_BUS_ARN") or "").strip()
    if bus_arn == "":
        _log_event("policy_trigger_skipped", reason="missing_event_bus_arn")
        return
    if _EVENTS is None:
        _log_event("policy_trigger_skipped", reason="events_client_unavailable")
        return

    detail = {
        "decision": str(decision.get("effect", "")),
        "rule_id": str(decision.get("rule_id", "")),
        "reason": str(decision.get("reason", "")),
        "caller_id": str(ctx.get("caller_id", "")),
        "account_email": str(ctx.get("account_email", "")),
        "method": str(ctx.get("method", "")),
        "host": str(ctx.get("host", "")),
        "path": str(ctx.get("path", "")),
        "status_code": int(status_code),
        "request_id": str(ctx.get("request_id", "")),
        "timestamp": int(time.time()),
    }
    extra_detail = on_match.get("detail") or {}
    if isinstance(extra_detail, dict):
        detail["extra"] = extra_detail

    try:
        _EVENTS.put_events(
            Entries=[
                {
                    "EventBusName": bus_arn,
                    "Source": "gogcli.proxy.policy",
                    "DetailType": str(on_match.get("event_type") or "proxy.policy.match"),
                    "Detail": json.dumps(detail, separators=(",", ":"), sort_keys=True),
                }
            ]
        )
    except Exception as e:  # pragma: no cover - best effort side effect
        _log_event("policy_trigger_error", error_type=type(e).__name__)


def authorize_policy(policy_prefix: str, policy_is_enabled: bool, policy_ctx: Mapping[str, Any]) -> Dict[str, Any]:
    policy_decision = {
        "effect": "allow",
        "rule_id": "",
        "source": "disabled",
        "reason": "policy_disabled",
        "on_match": {"emit_event": False, "event_type": "", "detail": {}},
    }
    if policy_is_enabled:
        policy = _load_effective_policy(policy_prefix, policy_ctx["caller_id"], policy_ctx["account_email"])
        policy_decision = _evaluate_policy(policy, policy_ctx)
        _log_event(
            "policy_decision",
            decision=policy_decision["effect"],
            rule_id=policy_decision["rule_id"],
            reason=policy_decision["reason"],
            source=policy_decision["source"],
            caller_id=policy_ctx["caller_id"],
            account_email=policy_ctx["account_email"],
            method=policy_ctx["method"],
            host=policy_ctx["host"],
            path=policy_ctx["path"],
        )
        if policy_decision["effect"] != "allow":
            _maybe_emit_policy_event(policy_decision, policy_ctx, 403)
            raise ProxyError(
                403,
                "policy_denied",
                extra={
                    "rule_id": policy_decision["rule_id"],
                    "reason": policy_decision["reason"],
                    "source": policy_decision["source"],
                },
            )
    else:
        _log_event("policy_decision", decision="allow", reason="policy_disabled", source="disabled")

    return policy_decision
