"""Validate and evaluate caller-visible proxy policy documents."""

import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from errors import ProxyError


_POLICY_TOP_KEYS = {"version", "default_effect", "rules"}
_POLICY_RULE_KEYS = {"id", "effect", "priority", "match", "constraints", "on_match"}
_POLICY_MATCH_KEYS = {"methods", "hosts", "path_regex", "query"}
_POLICY_QUERY_KEYS = {"require_keys", "forbid_keys"}
_POLICY_CONSTRAINT_KEYS = {"max_request_bytes", "require_account_domain"}
_POLICY_ON_MATCH_KEYS = {"emit_event", "event_type", "detail"}


def _as_lower_str_list(value: Any, *, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProxyError(500, f"policy field {field} must be a JSON array")
    out = []
    for item in value:
        s = str(item).strip().lower()
        if s != "":
            out.append(s)
    return out


def _as_upper_str_list(value: Any, *, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProxyError(500, f"policy field {field} must be a JSON array")
    out = []
    for item in value:
        s = str(item).strip().upper()
        if s != "":
            out.append(s)
    return out


def _normalize_policy_rule(rule: Any, *, source: str, source_rank: int, index: int) -> Dict[str, Any]:
    if not isinstance(rule, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] must be an object")

    unknown_rule = sorted(set(rule.keys()) - _POLICY_RULE_KEYS)
    if unknown_rule:
        raise ProxyError(500, f"policy rule {source}[{index}] has unknown keys: {','.join(unknown_rule)}")

    rule_id = str(rule.get("id") or f"{source}-rule-{index}").strip()
    if rule_id == "":
        raise ProxyError(500, f"policy rule {source}[{index}] has empty id")

    effect = str(rule.get("effect") or "deny").strip().lower()
    if effect not in {"allow", "deny"}:
        raise ProxyError(500, f"policy rule {source}[{index}] effect must be allow|deny")

    priority = rule.get("priority", 0)
    try:
        priority_int = int(priority)
    except (TypeError, ValueError):
        raise ProxyError(500, f"policy rule {source}[{index}] priority must be an integer")

    match = rule.get("match") or {}
    if not isinstance(match, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] match must be an object")

    unknown_match = sorted(set(match.keys()) - _POLICY_MATCH_KEYS)
    if unknown_match:
        raise ProxyError(500, f"policy rule {source}[{index}] match has unknown keys: {','.join(unknown_match)}")

    methods = sorted(set(_as_upper_str_list(match.get("methods"), field="methods")))
    hosts = sorted(set(_as_lower_str_list(match.get("hosts"), field="hosts")))

    path_regex = match.get("path_regex")
    if path_regex is None:
        path_regex_str = ""
    else:
        path_regex_str = str(path_regex).strip()
    if path_regex_str != "":
        try:
            re.compile(path_regex_str)
        except re.error as e:
            raise ProxyError(500, f"policy rule {source}[{index}] has invalid path_regex") from e

    query = match.get("query") or {}
    if not isinstance(query, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] query must be an object")
    unknown_query = sorted(set(query.keys()) - _POLICY_QUERY_KEYS)
    if unknown_query:
        raise ProxyError(500, f"policy rule {source}[{index}] query has unknown keys: {','.join(unknown_query)}")
    query_require_keys = sorted(set(_as_lower_str_list(query.get("require_keys"), field="require_keys")))
    query_forbid_keys = sorted(set(_as_lower_str_list(query.get("forbid_keys"), field="forbid_keys")))

    constraints = rule.get("constraints") or {}
    if not isinstance(constraints, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] constraints must be an object")
    unknown_constraints = sorted(set(constraints.keys()) - _POLICY_CONSTRAINT_KEYS)
    if unknown_constraints:
        raise ProxyError(
            500,
            f"policy rule {source}[{index}] constraints has unknown keys: {','.join(unknown_constraints)}",
        )

    max_request_bytes = constraints.get("max_request_bytes")
    if max_request_bytes is None:
        max_request_bytes_int = None
    else:
        try:
            max_request_bytes_int = int(max_request_bytes)
        except (TypeError, ValueError):
            raise ProxyError(500, f"policy rule {source}[{index}] max_request_bytes must be an integer")
        if max_request_bytes_int < 0:
            raise ProxyError(500, f"policy rule {source}[{index}] max_request_bytes must be >= 0")

    require_account_domain = sorted(
        set(_as_lower_str_list(constraints.get("require_account_domain"), field="require_account_domain"))
    )

    on_match = rule.get("on_match") or {}
    if not isinstance(on_match, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] on_match must be an object")
    unknown_on_match = sorted(set(on_match.keys()) - _POLICY_ON_MATCH_KEYS)
    if unknown_on_match:
        raise ProxyError(500, f"policy rule {source}[{index}] on_match has unknown keys: {','.join(unknown_on_match)}")

    emit_event = bool(on_match.get("emit_event", False))
    event_type = str(on_match.get("event_type") or f"proxy.policy.{effect}.{rule_id}").strip()
    if event_type == "":
        event_type = f"proxy.policy.{effect}.{rule_id}"
    detail = on_match.get("detail") or {}
    if not isinstance(detail, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] on_match.detail must be an object")

    return {
        "id": rule_id,
        "effect": effect,
        "priority": priority_int,
        "source": source,
        "source_rank": source_rank,
        "source_index": index,
        "match": {
            "methods": methods,
            "hosts": hosts,
            "path_regex": path_regex_str,
            "query_require_keys": query_require_keys,
            "query_forbid_keys": query_forbid_keys,
        },
        "constraints": {
            "max_request_bytes": max_request_bytes_int,
            "require_account_domain": require_account_domain,
        },
        "on_match": {
            "emit_event": emit_event,
            "event_type": event_type,
            "detail": detail,
        },
    }


def _normalize_policy_document(doc: Any, *, source: str, source_rank: int) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise ProxyError(500, f"policy {source} must be a JSON object")

    unknown_keys = sorted(set(doc.keys()) - _POLICY_TOP_KEYS)
    if unknown_keys:
        raise ProxyError(500, f"policy {source} has unknown keys: {','.join(unknown_keys)}")

    version_raw = doc.get("version", 1)
    try:
        version = int(version_raw)
    except (TypeError, ValueError):
        raise ProxyError(500, f"policy {source} version must be an integer")
    if version != 1:
        raise ProxyError(500, f"policy {source} has unsupported version: {version}")

    default_effect = str(doc.get("default_effect") or "deny").strip().lower()
    if default_effect not in {"allow", "deny"}:
        raise ProxyError(500, f"policy {source} default_effect must be allow|deny")

    rules = doc.get("rules") or []
    if not isinstance(rules, list):
        raise ProxyError(500, f"policy {source} rules must be a JSON array")

    normalized_rules = [
        _normalize_policy_rule(rule, source=source, source_rank=source_rank, index=idx) for idx, rule in enumerate(rules)
    ]

    return {
        "version": version,
        "default_effect": default_effect,
        "rules": normalized_rules,
        "source": source,
        "source_rank": source_rank,
    }


def _merge_policy_documents(docs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {"version": 1, "default_effect": "deny", "rules": []}
    for doc in docs:
        merged["default_effect"] = doc["default_effect"]
        merged["rules"].extend(doc["rules"])

    merged["rules"].sort(
        key=lambda r: (
            -int(r["priority"]),
            -int(r["source_rank"]),
            int(r["source_index"]),
        )
    )
    return merged


def _rule_match(rule: Mapping[str, Any], ctx: Mapping[str, Any]) -> Tuple[bool, str]:
    match = rule["match"]
    constraints = rule["constraints"]

    methods = match["methods"]
    if methods and ctx["method"] not in methods:
        return False, "method_not_allowed"

    hosts = match["hosts"]
    if hosts and ctx["host"] not in hosts:
        return False, "host_not_allowed"

    path_regex = match["path_regex"]
    if path_regex != "" and re.match(path_regex, ctx["path"]) is None:
        return False, "path_not_allowed"

    query_keys = set(ctx["query_keys"])
    for req_key in match["query_require_keys"]:
        if req_key not in query_keys:
            return False, "required_query_key_missing"
    for forbid_key in match["query_forbid_keys"]:
        if forbid_key in query_keys:
            return False, "forbidden_query_key_present"

    max_request_bytes = constraints["max_request_bytes"]
    if max_request_bytes is not None and int(ctx["request_size_bytes"]) > int(max_request_bytes):
        return False, "request_too_large"

    required_domains = constraints["require_account_domain"]
    if required_domains and ctx["account_domain"] not in required_domains:
        return False, "account_domain_not_allowed"

    return True, "matched"


def _evaluate_policy(policy: Mapping[str, Any], ctx: Mapping[str, Any]) -> Dict[str, Any]:
    for rule in policy["rules"]:
        matched, reason = _rule_match(rule, ctx)
        if not matched:
            continue
        return {
            "effect": rule["effect"],
            "rule_id": rule["id"],
            "source": rule["source"],
            "reason": reason,
            "on_match": rule["on_match"],
        }

    return {
        "effect": policy["default_effect"],
        "rule_id": "",
        "source": "default_effect",
        "reason": "default_effect",
        "on_match": {"emit_event": False, "event_type": "", "detail": {}},
    }
