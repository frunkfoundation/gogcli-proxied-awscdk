"""Proxy configuration with bounded warm-runtime SSM caches."""
import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from errors import ProxyError

_SSM = boto3.client("ssm")
# Key: parameter name; value: JSON or None, expiry in epoch seconds.
_CACHE: Dict[str, Tuple[Optional[Dict[str, Any]], float]] = {}


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
    except ClientError as e:
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
