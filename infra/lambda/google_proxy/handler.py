import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from credentials import mint_access_token
from errors import ProxyError, remaining_seconds
from policy import _evaluate_policy, _compose_policy_documents, _normalize_policy_document
from proxy_http import (
    _build_query_keys, _build_query_string, _extract_account_domain, _get_header,
    _normalize_email, _read_event_body, _sanitize_outgoing_headers, read_response, request_headers,
)
from routing import RoutingError, resolve_google_host_and_path
from urllib.parse import urlsplit
from inventory import caller_accounts

try:
    import boto3
except Exception:  # pragma: no cover - runtime dependency in Lambda
    boto3 = None

try:
    import botocore.exceptions as botocore_exceptions
except Exception:  # pragma: no cover - local test fallback
    class _FallbackClientError(Exception):
        pass

    class _FallbackBotocoreExceptions:
        ClientError = _FallbackClientError

    botocore_exceptions = _FallbackBotocoreExceptions()

try:
    import requests
except Exception:  # pragma: no cover - local test fallback
    class _RequestsFallback:
        class Session:
            def request(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("requests unavailable")

    requests = _RequestsFallback()

_SSM = boto3.client("ssm") if boto3 is not None else None
_EVENTS = boto3.client("events") if boto3 is not None else None
_HTTP = requests.Session()


def _json_response(status_code: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        },
        "body": body,
        "isBase64Encoded": False,
    }


def _log_event(event_type: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {"event": event_type}
    payload.update(fields)
    try:
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    except Exception:
        # Last-resort log safety to avoid breaking request flow.
        print(f'{{"event":"{event_type}","log_error":"serialization_failed"}}')


def _parse_bool_env(raw: Optional[str], *, default: bool) -> bool:
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if val in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _policy_enabled() -> bool:
    return _parse_bool_env(os.getenv("GOG_POLICY_ENABLED"), default=True)


def _policy_cache_ttl_seconds() -> int:
    raw = os.getenv("GOG_POLICY_CACHE_TTL_SECONDS") or "30"
    try:
        val = int(raw)
    except ValueError:
        return 30
    if val < 1:
        return 1
    return min(val, 30)


def _policy_events_enabled() -> bool:
    return _parse_bool_env(os.getenv("GOG_POLICY_EVENTS_ENABLED"), default=False)


def _policy_ssm_prefix(default_ssm_prefix: str) -> str:
    override = (os.getenv("GOG_POLICY_SSM_PREFIX") or "").strip()
    if override != "":
        return override.rstrip("/")
    return f"{default_ssm_prefix.rstrip('/')}/policy"


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


def _ssm_get_json(name: str, *, cache_ttl_seconds: int = 60) -> Optional[Dict[str, Any]]:
    if _SSM is None:
        raise ProxyError(500, "ssm client unavailable in runtime")

    cache_ttl_seconds = max(1, min(cache_ttl_seconds, 60))
    now = time.time()
    cached = _CACHE.get(name)
    if cached is not None:
        value, exp = cached
        if exp > now:
            return value
        _CACHE.pop(name, None)

    try:
        resp = _SSM.get_parameter(Name=name, WithDecryption=True)
    except botocore_exceptions.ClientError as e:
        code = (e.response.get("Error") or {}).get("Code") if hasattr(e, "response") else None
        if code in ("ParameterNotFound",):
            _CACHE[name] = (None, now + cache_ttl_seconds)
            return None
        raise

    raw = (resp.get("Parameter") or {}).get("Value")
    if raw is None:
        _CACHE[name] = (None, now + cache_ttl_seconds)
        return None
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProxyError(500, f"invalid JSON in SSM parameter: {name}") from e
    if not isinstance(out, dict):
        raise ProxyError(500, f"SSM parameter must be a JSON object: {name}")

    _CACHE[name] = (out, now + cache_ttl_seconds)
    return out


def _ssm_get_policy_json(name: str) -> Optional[Dict[str, Any]]:
    return _ssm_get_json(name, cache_ttl_seconds=_policy_cache_ttl_seconds())


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
        raw = _ssm_get_policy_json(name)
        if raw is None:
            continue
        docs.append(_normalize_policy_document(raw, source=source, source_rank=rank))
    if not any(doc["source"] == "default" for doc in docs):
        raise ProxyError(403, "policy_not_found")
    return _compose_policy_documents(docs)


def _maybe_emit_policy_event(decision: Mapping[str, Any], ctx: Mapping[str, Any], status_code: int) -> None:
    if not _policy_events_enabled():
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


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        budget = min(27.0, context.get_remaining_time_in_millis() / 1000 - 2) if context is not None else 27.0
        deadline = time.monotonic() + budget
        remaining_seconds(deadline)
        ssm_prefix = (os.getenv("GOG_SSM_PREFIX") or "/gogcli-proxy").rstrip("/")
        policy_prefix = _policy_ssm_prefix(ssm_prefix)
        policy_is_enabled = _policy_enabled()
        max_req = min(int(os.getenv("GOG_MAX_REQUEST_BYTES") or str(4 * 1024 * 1024)), 4 * 1024 * 1024)
        max_resp = int(os.getenv("GOG_MAX_RESPONSE_BYTES") or "5500000")

        headers = request_headers(event)
        caller = _caller_id(event)
        allow_param = f"{ssm_prefix}/allow/{_b64url_nopad(caller)}"
        allow_cfg = _ssm_get_json(allow_param)
        if allow_cfg is None:
            raise ProxyError(403, "caller account permission is not configured")
        _validate_allowlist(allow_cfg)
        method = str(event.get("httpMethod") or "GET").upper()
        pp = event.get("pathParameters") or {}
        proxy = pp.get("proxy") if isinstance(pp, dict) else None
        path = "/" + str(proxy) if proxy else str(event.get("path") or "/")
        if path.startswith("/proxy/v1/"):
            if path != "/proxy/v1/accounts":
                raise ProxyError(404, "unknown proxy route")
            if method != "GET":
                raise ProxyError(405, "method not allowed")
            return _json_response(200, caller_accounts(allow_cfg, _is_allowed, deadline=deadline))
        account = _normalize_email(_get_header(headers, "X-GOG-Account"))
        if re.fullmatch(r"[^\s,@]+@[^\s,@]+", account) is None:
            raise ProxyError(400, "missing or invalid X-GOG-Account header")
        if not _is_allowed(allow_cfg, account):
            raise ProxyError(403, "caller not authorized for requested account")

        acct_param = f"{ssm_prefix}/google/accounts/{_b64url_nopad(account)}"
        acct_cfg = _ssm_get_json(acct_param)
        if acct_cfg is None:
            raise ProxyError(403, "requested account is not configured in proxy")

        try:
            host, forward_path = resolve_google_host_and_path(path, _get_header(headers, "X-GOG-Google-Host"))
        except RoutingError as e:
            raise ProxyError(400, str(e)) from e
        query = _build_query_string(event)
        url = f"https://{host}{forward_path}"
        if query:
            url = f"{url}?{query}"

        body_bytes = _read_event_body(event, max_bytes=max_req)
        query_keys = _build_query_keys(event)
        out_headers = _sanitize_outgoing_headers(headers)
        prepared = requests.Request(method, url, headers=out_headers, data=body_bytes if body_bytes else None).prepare()
        destination = urlsplit(prepared.url)
        # requests canonicalizes escapes; authorization uses exactly that prepared path.
        try:
            checked_host, checked_path = resolve_google_host_and_path(destination.path, destination.hostname or "")
        except RoutingError as e:
            raise ProxyError(400, str(e)) from e
        if checked_host != host or checked_path != destination.path:
            raise ProxyError(400, "ambiguous prepared destination")
        forward_path = destination.path
        request_id = str((event.get("requestContext") or {}).get("requestId") or "")
        policy_decision = {
            "effect": "allow",
            "rule_id": "",
            "source": "disabled",
            "reason": "policy_disabled",
            "on_match": {"emit_event": False, "event_type": "", "detail": {}},
        }
        policy_ctx = {
            "caller_id": caller,
            "account_email": account,
            "account_domain": _extract_account_domain(account),
            "method": method,
            "host": host.lower(),
            "path": forward_path,
            "query_keys": query_keys,
            "request_size_bytes": len(body_bytes),
            "request_id": request_id,
        }

        if policy_is_enabled:
            policy = _load_effective_policy(policy_prefix, caller, account)
            policy_decision = _evaluate_policy(policy, policy_ctx)
            _log_event(
                "policy_decision",
                decision=policy_decision["effect"],
                rule_id=policy_decision["rule_id"],
                reason=policy_decision["reason"],
                source=policy_decision["source"],
                caller_id=caller,
                account_email=account,
                method=method,
                host=host.lower(),
                path=forward_path,
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

        token = mint_access_token(account, acct_cfg, _HTTP, timeout=remaining_seconds(deadline, limit=20))

        prepared.headers["Authorization"] = f"Bearer {token}"

        upstream_budget = remaining_seconds(deadline)
        connect_timeout = min(5.0, upstream_budget / 2)
        resp = _HTTP.send(
            prepared,
            stream=True,
            allow_redirects=False,
            timeout=(connect_timeout, upstream_budget - connect_timeout),
        )

        response = read_response(resp, max_bytes=max_resp, deadline=deadline)
        if policy_is_enabled:
            _maybe_emit_policy_event(policy_decision, policy_ctx, int(resp.status_code))
        return response

    except ProxyError as e:
        payload = {"error": e.message}
        payload.update(e.extra)
        return _json_response(e.status_code, payload)
    except Exception as e:
        # Avoid leaking secrets in exception strings.
        _log_event("proxy_internal_error", error_type=type(e).__name__)
        return _json_response(500, {"error": "internal proxy error"})


# Simple in-memory cache for SSM JSON blobs.
# Key: param name; Value: (json or None, expires_epoch_seconds)
_CACHE: Dict[str, Tuple[Optional[Dict[str, Any]], float]] = {}
