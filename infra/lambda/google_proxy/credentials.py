"""Mint and reuse per-account Google access tokens in a warm Lambda runtime."""

import hashlib
import json
import math
import os
import time
from datetime import timezone
from typing import Any, Dict, List, Mapping, Tuple

from errors import ProxyError
from proxy_http import _normalize_email

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover - dependency installed in the Lambda bundle
    GoogleAuthRequest = None
    service_account = None


# One token per account; replacing a config evicts its old token immediately.
_TOKEN_CACHE: Dict[str, Tuple[str, str, float]] = {}


def _cache_token(account: str, fingerprint: str, token: str, expiry: float) -> None:
    if math.isfinite(expiry) and expiry > time.time() + 60:
        _TOKEN_CACHE[account] = (fingerprint, token, expiry)


def _default_scopes() -> List[str]:
    raw = os.getenv("GOG_DEFAULT_SCOPES", "")
    scopes = [s.strip() for s in raw.split(",") if s.strip()]
    # Keep stable ordering for readability/debugging.
    return sorted(set(scopes))


def mint_access_token(account_email: str, cfg: Mapping[str, Any], http: Any, *, timeout: float = 20) -> str:
    account_email = _normalize_email(account_email)
    fingerprint = hashlib.sha256(json.dumps([cfg, _default_scopes()], sort_keys=True).encode()).hexdigest()
    cached = _TOKEN_CACHE.get(account_email)
    if cached is not None:
        old_fingerprint, token, expiry = cached
        if old_fingerprint == fingerprint and expiry > time.time() + 60:
            return token
        _TOKEN_CACHE.pop(account_email, None)

    typ = str(cfg.get("type") or "").strip().lower()
    if typ == "":
        raise ProxyError(500, "google credential config missing required field: type")

    if typ == "service_account":
        if GoogleAuthRequest is None or service_account is None:
            raise ProxyError(500, "google service_account dependencies unavailable in runtime")
        info = cfg.get("service_account") or cfg.get("serviceAccount") or cfg.get("service_account_info") or cfg.get("serviceAccountInfo")
        if not isinstance(info, dict):
            raise ProxyError(500, "service_account config missing service_account object")

        scopes = cfg.get("scopes")
        if scopes is None:
            scope_list = _default_scopes()
        elif isinstance(scopes, list):
            scope_list = [str(s).strip() for s in scopes if str(s).strip()]
        else:
            raise ProxyError(500, "service_account scopes must be a JSON array of strings")

        delegate_subject = cfg.get("delegate_subject")
        if delegate_subject is None:
            subject = _normalize_email(account_email)
        else:
            subject = str(delegate_subject).strip()
            if subject == "":
                subject = None  # no domain-wide delegation

        if subject:
            creds = service_account.Credentials.from_service_account_info(info, scopes=scope_list, subject=subject)
        else:
            creds = service_account.Credentials.from_service_account_info(info, scopes=scope_list)

        try:
            deadline = time.monotonic() + timeout
            request = GoogleAuthRequest(session=http)

            def bounded_request(*args: Any, **kwargs: Any) -> Any:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Google token refresh deadline exceeded")
                connect = min(5.0, remaining / 2)
                kwargs["timeout"] = (connect, remaining - connect)
                return request(*args, **kwargs)

            creds.refresh(bounded_request)
        except Exception as e:
            raise ProxyError(502, "failed to mint Google access token") from e

        token = str(getattr(creds, "token", "") or "").strip()
        if not token:
            raise ProxyError(502, "Google access token missing after refresh")
        expiry = getattr(creds, "expiry", None)
        if expiry is not None:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            _cache_token(account_email, fingerprint, token, expiry.timestamp())
        return token

    elif typ == "oauth_refresh":
        client_id = str(cfg.get("client_id") or "").strip()
        client_secret = str(cfg.get("client_secret") or "").strip()
        refresh_token = str(cfg.get("refresh_token") or "").strip()
        token_uri = str(cfg.get("token_uri") or "https://oauth2.googleapis.com/token").strip()

        if not client_id or not client_secret or not refresh_token:
            raise ProxyError(500, "oauth_refresh config missing client_id/client_secret/refresh_token")

        scopes = cfg.get("scopes")
        if scopes is None:
            scope_str = ""
        elif isinstance(scopes, list):
            scope_str = " ".join(str(s).strip() for s in scopes if str(s).strip())
        else:
            raise ProxyError(500, "oauth_refresh scopes must be a JSON array of strings")

        form_data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        if scope_str:
            form_data["scope"] = scope_str

        try:
            connect = min(5.0, timeout / 2)
            token_resp = http.post(token_uri, data=form_data, timeout=(connect, timeout - connect))
        except Exception as e:
            raise ProxyError(502, "failed to mint Google access token") from e

        if int(getattr(token_resp, "status_code", 0) or 0) != 200:
            try:
                revoked = token_resp.json().get("error") == "invalid_grant"
            except (ValueError, AttributeError, TypeError):
                revoked = False
            if revoked:
                raise ProxyError(401, "refresh_revoked")
            raise ProxyError(502, "failed to mint Google access token")

        try:
            token_json = token_resp.json()
        except Exception as e:
            raise ProxyError(502, "failed to parse Google token response") from e

        token = str(token_json.get("access_token") or "").strip()
        if not token:
            raise ProxyError(502, "Google access token missing after refresh")
        try:
            expires_in = float(token_json.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        _cache_token(account_email, fingerprint, token, time.time() + expires_in)
        return token

    else:
        raise ProxyError(500, f"unsupported google credential type: {typ}")
