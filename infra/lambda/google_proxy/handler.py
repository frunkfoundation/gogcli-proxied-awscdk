"""Prepare, authorize, and forward bounded requests to Google."""
import json
import os
import re
import time
from typing import Any, Dict
from urllib.parse import urlsplit

import requests
import proxy_authorization as authorization
import proxy_config
from credentials import mint_access_token
from downloads import read_proxy_response
from errors import ProxyError, remaining_seconds
from inventory import caller_accounts
from proxy_http import (
    _build_query_keys, _build_query_string, _extract_account_domain, _get_header,
    _normalize_email, _read_event_body, _sanitize_outgoing_headers, request_headers,
)
from routing import RoutingError, resolve_google_host_and_path

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


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        budget = min(27.0, context.get_remaining_time_in_millis() / 1000 - 2) if context is not None else 27.0
        deadline = time.monotonic() + budget
        remaining_seconds(deadline)
        ssm_prefix = (os.getenv("GOG_SSM_PREFIX") or "/gogcli-proxy").rstrip("/")
        policy_prefix = proxy_config._policy_ssm_prefix(ssm_prefix)
        policy_is_enabled = proxy_config._policy_enabled()
        max_req = min(int(os.getenv("GOG_MAX_REQUEST_BYTES") or str(4 * 1024 * 1024)), 4 * 1024 * 1024)
        max_resp = int(os.getenv("GOG_MAX_RESPONSE_BYTES") or "5500000")

        headers = request_headers(event)
        caller = authorization._caller_id(event)
        allow_param = f"{ssm_prefix}/allow/{authorization._b64url_nopad(caller)}"
        allow_cfg = proxy_config._ssm_get_json(allow_param)
        if allow_cfg is None:
            raise ProxyError(403, "caller account permission is not configured")
        authorization._validate_allowlist(allow_cfg)
        method = str(event.get("httpMethod") or "GET").upper()
        pp = event.get("pathParameters") or {}
        proxy = pp.get("proxy") if isinstance(pp, dict) else None
        path = "/" + str(proxy) if proxy else str(event.get("path") or "/")
        if path.startswith("/proxy/v1/"):
            if path != "/proxy/v1/accounts":
                raise ProxyError(404, "unknown proxy route")
            if method != "GET":
                raise ProxyError(405, "method not allowed")
            return _json_response(200, caller_accounts(allow_cfg, authorization._is_allowed, deadline=deadline))
        account = _normalize_email(_get_header(headers, "X-GOG-Account"))
        if re.fullmatch(r"[^\s,@]+@[^\s,@]+", account) is None:
            raise ProxyError(400, "missing or invalid X-GOG-Account header")
        if not authorization._is_allowed(allow_cfg, account):
            raise ProxyError(403, "caller not authorized for requested account")

        acct_param = f"{ssm_prefix}/google/accounts/{authorization._b64url_nopad(account)}"
        acct_cfg = proxy_config._ssm_get_json(acct_param)
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

        policy_decision = authorization.authorize_policy(policy_prefix, policy_is_enabled, policy_ctx)

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

        response = read_proxy_response(resp, method=method, url=prepared.url, headers=headers,
                                       max_bytes=max_resp, deadline=deadline)
        if policy_is_enabled:
            authorization._maybe_emit_policy_event(policy_decision, policy_ctx, int(resp.status_code))
        return response

    except ProxyError as e:
        payload = {"error": e.message}
        payload.update(e.extra)
        return _json_response(e.status_code, payload)
    except Exception as e:
        # Avoid leaking secrets in exception strings.
        authorization._log_event("proxy_internal_error", error_type=type(e).__name__)
        return _json_response(500, {"error": "internal proxy error"})
