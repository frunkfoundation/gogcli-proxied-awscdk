"""Validate and evaluate caller-visible proxy policy documents."""

import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from errors import ProxyError


_POLICY_TOP_KEYS = {"version", "default_effect", "rules"}
_POLICY_RULE_KEYS = {"id", "effect", "priority", "match", "constraints", "on_match"}
_POLICY_MATCH_KEYS = {"methods", "hosts", "path_regex", "query", "accounts"}
_POLICY_QUERY_KEYS = {"require_keys", "forbid_keys"}
_POLICY_CONSTRAINT_KEYS = {"max_request_bytes", "require_account_domain"}
_POLICY_ON_MATCH_KEYS = {"emit_event", "event_type", "detail"}


def _string_list(value: Any, *, field: str, upper=False) -> List[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProxyError(500, f"policy field {field} must be an array of nonempty strings")
    return [item.strip().upper() if upper else item.strip().lower() for item in value]


def _as_lower_str_list(value: Any, *, field: str) -> List[str]:
    return _string_list(value, field=field)


def _as_upper_str_list(value: Any, *, field: str) -> List[str]:
    return _string_list(value, field=field, upper=True)


def _integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProxyError(500, f"policy field {field} must be an integer")
    return value


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

    priority_int = _integer(rule.get("priority", 0), field="priority")

    match = rule.get("match", {})
    if not isinstance(match, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] match must be an object")

    unknown_match = sorted(set(match.keys()) - _POLICY_MATCH_KEYS)
    if unknown_match:
        raise ProxyError(500, f"policy rule {source}[{index}] match has unknown keys: {','.join(unknown_match)}")

    methods = sorted(set(_as_upper_str_list(match.get("methods", []), field="methods")))
    hosts = sorted(set(_as_lower_str_list(match.get("hosts", []), field="hosts")))
    accounts = sorted(set(_as_lower_str_list(match.get("accounts", []), field="accounts")))
    if any(re.fullmatch(r"[^\s,@/]+@[^\s,@/]+\.[^\s,@/]+", email) is None for email in accounts):
        raise ProxyError(500, "policy accounts must contain exact Google account emails")

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

    query = match.get("query", {})
    if not isinstance(query, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] query must be an object")
    unknown_query = sorted(set(query.keys()) - _POLICY_QUERY_KEYS)
    if unknown_query:
        raise ProxyError(500, f"policy rule {source}[{index}] query has unknown keys: {','.join(unknown_query)}")
    query_require_keys = sorted(set(_as_lower_str_list(query.get("require_keys", []), field="require_keys")))
    query_forbid_keys = sorted(set(_as_lower_str_list(query.get("forbid_keys", []), field="forbid_keys")))

    constraints = rule.get("constraints", {})
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
        max_request_bytes_int = _integer(max_request_bytes, field="max_request_bytes")
        if max_request_bytes_int < 0:
            raise ProxyError(500, f"policy rule {source}[{index}] max_request_bytes must be >= 0")

    require_account_domain = sorted(
        set(_as_lower_str_list(constraints.get("require_account_domain", []), field="require_account_domain"))
    )

    on_match = rule.get("on_match", {})
    if not isinstance(on_match, dict):
        raise ProxyError(500, f"policy rule {source}[{index}] on_match must be an object")
    unknown_on_match = sorted(set(on_match.keys()) - _POLICY_ON_MATCH_KEYS)
    if unknown_on_match:
        raise ProxyError(500, f"policy rule {source}[{index}] on_match has unknown keys: {','.join(unknown_on_match)}")

    emit_event = bool(on_match.get("emit_event", False))
    event_type = str(on_match.get("event_type") or f"proxy.policy.{effect}.{rule_id}").strip()
    if event_type == "":
        event_type = f"proxy.policy.{effect}.{rule_id}"
    detail = on_match.get("detail", {})
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
            "accounts": accounts,
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

    version = _integer(doc.get("version", 1), field="version")
    if version != 1:
        raise ProxyError(500, f"policy {source} has unsupported version: {version}")

    default_effect = str(doc.get("default_effect") or "deny").strip().lower()
    if default_effect not in {"allow", "deny"}:
        raise ProxyError(500, f"policy {source} default_effect must be allow|deny")

    rules = doc.get("rules", [])
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


def _compose_policy_documents(docs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {doc["source"]: doc for doc in docs}


def _rule_match(rule: Mapping[str, Any], ctx: Mapping[str, Any]) -> Tuple[bool, str]:
    match = rule["match"]
    constraints = rule["constraints"]

    accounts = match["accounts"]
    if accounts and ctx["account_email"] not in accounts:
        return False, "account_not_allowed"

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


def _decision(doc: Mapping[str, Any], rule=None) -> Dict[str, Any]:
    return {
        "effect": rule["effect"] if rule else doc["default_effect"],
        "rule_id": rule["id"] if rule else "",
        "source": doc["source"],
        "reason": "matched" if rule else "default_effect",
        "on_match": rule["on_match"] if rule else {"emit_event": False, "event_type": "", "detail": {}},
    }


def _evaluate_policy(policy: Mapping[str, Any], ctx: Mapping[str, Any]) -> Dict[str, Any]:
    default = policy.get("default")
    if default is None:
        raise ProxyError(403, "policy_not_found")
    matches = {}
    for source, doc in policy.items():
        rules = sorted(doc["rules"], key=lambda r: (-r["priority"], r["source_index"]))
        matches[source] = [rule for rule in rules if _rule_match(rule, ctx)[0]]
        for rule in matches[source]:
            if rule["effect"] == "deny":
                return _decision(doc, rule)
    source = "caller" if "caller" in policy else "default"
    doc = policy[source]
    decision = _decision(doc, matches[source][0] if matches[source] else None)
    if decision["effect"] != "allow":
        return decision
    if "account" in policy:
        doc = policy["account"]
        ceiling = _decision(doc, matches["account"][0] if matches["account"] else None)
        if ceiling["effect"] != "allow":
            return ceiling
    return decision
