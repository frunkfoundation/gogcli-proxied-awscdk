"""HTTP request and response adaptation for API Gateway and Google."""

import base64
import json
import re
from typing import Any, Dict, List, Mapping, Tuple

from errors import ProxyError, remaining_seconds


def _normalize_email(s: str) -> str:
    return s.strip().lower()


def _get_header(headers: Mapping[str, Any], name: str) -> str:
    want = name.lower()
    for k, v in headers.items():
        if k is None:
            continue
        if str(k).lower() == want and v is not None:
            return str(v)
    return ""


def _extract_account_domain(account_email: str) -> str:
    email = _normalize_email(account_email)
    if "@" not in email:
        return ""
    return email.split("@", 1)[1]


def request_headers(event: Mapping[str, Any]) -> Dict[str, str]:
    single = event.get("headers") or {}
    multi = event.get("multiValueHeaders") or {}
    if not isinstance(single, dict) or not isinstance(multi, dict):
        raise ProxyError(400, "invalid request headers")
    normalized = {}
    for representation in (single, multi):
        seen = set()
        for key, value in representation.items():
            if not isinstance(key, str) or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None:
                raise ProxyError(400, "invalid request headers")
            name = key.lower()
            if name in seen:
                raise ProxyError(400, "ambiguous request headers")
            seen.add(name)
            if name in {"x-http-method-override", "x-http-method", "x-method-override"}:
                raise ProxyError(400, "method overrides are unsupported")
            values = value if isinstance(value, list) else [value]
            if len(values) != 1 or values[0] is None:
                raise ProxyError(400, "ambiguous request headers")
            text = str(values[0])
            if any(ord(char) < 32 or ord(char) == 127 for char in text):
                raise ProxyError(400, "invalid request headers")
            if name in normalized and normalized[name] != text:
                raise ProxyError(400, "contradictory request headers")
            normalized[name] = text
    return normalized


def _query_pairs(event: Mapping[str, Any]) -> List[Tuple[str, str]]:
    single = event.get("queryStringParameters") or {}
    multi = event.get("multiValueQueryStringParameters")
    if not isinstance(single, dict) or (multi is not None and not isinstance(multi, dict)):
        raise ProxyError(400, "invalid query parameters")
    if multi is not None:
        normalized = {str(key): values for key, values in multi.items()}
        if single and (set(single) != set(normalized) or any(
            not isinstance(normalized[key], list) or not normalized[key]
            or str(value) != str(normalized[key][-1]) for key, value in single.items()
        )):
            raise ProxyError(400, "contradictory query parameters")
        pairs = []
        for key, values in normalized.items():
            if not isinstance(values, list) or not values or any(value is None for value in values):
                raise ProxyError(400, "invalid query parameters")
            pairs.extend((key, str(value)) for value in values)
        return pairs
    return [(str(key), str(value)) for key, value in single.items() if value is not None]


def _build_query_keys(event: Mapping[str, Any]) -> List[str]:
    return sorted({key.strip().lower() for key, _ in _query_pairs(event) if key.strip()})


def _build_query_string(event: Mapping[str, Any]) -> str:
    from urllib.parse import urlencode
    return urlencode(_query_pairs(event), safe="~")


def _sanitize_outgoing_headers(in_headers: Mapping[str, Any]) -> Dict[str, str]:
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
    strip = {
        "host",
        "authorization",
        "x-amz-date",
        "x-amz-security-token",
        "x-api-key",
        "x-gog-account",
        "x-gog-google-host",
        "x-gog-response-mode",
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-port",
        "via",
        "content-length",
    }

    out: Dict[str, str] = {}
    for k, v in in_headers.items():
        if k is None or v is None:
            continue
        key = str(k).strip()
        if key == "":
            continue
        key_lc = key.lower()
        if key_lc in hop_by_hop or key_lc in strip:
            continue
        out[key] = str(v)
    return out


def _is_textual_content_type(content_type: str) -> bool:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct == "":
        return True
    if ct.startswith("text/"):
        return True
    if ct in ("application/json", "application/xml", "application/x-www-form-urlencoded"):
        return True
    if ct.endswith("+json") or ct.endswith("+xml"):
        return True
    return False


def _sanitize_response_headers(in_headers: Mapping[str, Any]) -> Dict[str, str]:
    strip = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        # requests may transparently decompress; avoid lying to the client.
        "content-encoding",
    }
    out: Dict[str, str] = {}
    for k, v in in_headers.items():
        if k is None or v is None:
            continue
        key = str(k).strip()
        if key == "":
            continue
        if key.lower() in strip:
            continue
        out[key] = str(v)
    return out


def _read_event_body(event: Mapping[str, Any], *, max_bytes: int) -> bytes:
    body = event.get("body")
    if body is None:
        return b""
    if not isinstance(body, str):
        body = str(body)

    if event.get("isBase64Encoded") is True:
        try:
            raw = base64.b64decode(body)
        except Exception as e:
            raise ProxyError(400, "invalid base64 request body") from e
    else:
        raw = body.encode("utf-8")

    if len(raw) > max_bytes:
        raise ProxyError(413, "request body too large for proxy")
    return raw


def read_response(resp: Any, *, max_bytes: int, deadline: float) -> Dict[str, Any]:
    try:
        # Bound raw memory use before encoding and checking the full envelope.
        chunks: List[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            remaining_seconds(deadline)
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ProxyError(502, "upstream response too large for Lambda")
            chunks.append(chunk)
        payload = b"".join(chunks)

        resp_headers = _sanitize_response_headers(resp.headers)
        content_type = _get_header(resp_headers, "Content-Type")

        if _is_textual_content_type(content_type):
            body_out = payload.decode("utf-8", errors="replace")
            is_b64 = False
        else:
            body_out = base64.b64encode(payload).decode("ascii")
            is_b64 = True

        response = {
            "statusCode": int(resp.status_code),
            "headers": resp_headers,
            "body": body_out,
            "isBase64Encoded": is_b64,
        }
        # Include base64 expansion, JSON escaping, headers, and the Lambda envelope.
        if len(json.dumps(response).encode("utf-8")) > 6 * 1024 * 1024:
            raise ProxyError(502, "upstream response too large for Lambda")
        remaining_seconds(deadline)
        return response
    finally:
        resp.close()
