"""The supported consent capabilities, bounded probes, and safe account projection."""
import hashlib
import json

from onboarding_store import WorkflowError

SERVICE_OPTIONS = [
    {"name": "gmail", "label": "Gmail", "apiName": "gmail.googleapis.com", "scope": "https://www.googleapis.com/auth/gmail.modify",
     "description": "Read, send, label, and change mail."},
    {"name": "drive", "label": "Google Drive", "apiName": "drive.googleapis.com", "scope": "https://www.googleapis.com/auth/drive",
     "description": "Read and change existing Drive files."},
    {"name": "calendar", "label": "Google Calendar", "apiName": "calendar-json.googleapis.com",
     "scope": "https://www.googleapis.com/auth/calendar",
     "description": "Read, create, change, share, and delete calendars and events."},
]
IDENTITY_SCOPES = ["openid", "email"]
SCOPES_BY_SERVICE = {item["name"]: item["scope"] for item in SERVICE_OPTIONS}
DEFAULT_SERVICES = ["gmail", "drive"]
KNOWN_ACCOUNTS = {
    "jay.jakosky@gmail.com": "jayj-gogcli-full-access",
    "jay@frunkholdings.ltd": "jay-fhl-gogcli-full-access",
    "treasurer@longmonttheatre.org": "gogcli-ltc-treasurer-full",
}


def selected_services(value):
    if not isinstance(value, list) or not value or any(not isinstance(name, str) or name not in SCOPES_BY_SERVICE for name in value):
        raise WorkflowError(400, "invalid_services", "Select Gmail, Drive, Calendar, or a combination.")
    return [name for name in SCOPES_BY_SERVICE if name in value]


def requested_scopes(services):
    return IDENTITY_SCOPES + [SCOPES_BY_SERVICE[name] for name in selected_services(services)]


def normalized_scopes(value):
    scopes = set(str(value).split())
    if "https://www.googleapis.com/auth/userinfo.email" in scopes:
        scopes.add("email")
    return sorted(scopes)


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def service_policy(services):
    rules = []
    for name in selected_services(services):
        paths, hosts = {
            "gmail": (r"^/gmail/v1/", ["gmail.googleapis.com", "www.googleapis.com"]),
            "drive": (r"^/(?:drive/v3/|upload/drive/v3/|resumable/upload/drive/v3/)", ["www.googleapis.com"]),
            "calendar": (r"^/calendar/v3/", ["calendar.googleapis.com", "www.googleapis.com"]),
        }[name]
        rules.append({"id": name, "effect": "allow", "match": {"hosts": hosts,
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE"], "path_regex": paths}})
    return {"version": 1, "default_effect": "deny", "rules": rules}


def verifier_policy():
    return {"version": 1, "default_effect": "deny", "rules": [
        {"id": "gmail-probes", "effect": "allow", "match": {"hosts": ["gmail.googleapis.com", "www.googleapis.com"],
            "methods": ["GET"], "path_regex": r"^/gmail/v1/users/me/(profile|messages)$"}},
        {"id": "drive-probes", "effect": "allow", "match": {"hosts": ["www.googleapis.com"],
            "methods": ["GET"], "path_regex": r"^/drive/v3/(about|files)$"}},
        {"id": "calendar-probes", "effect": "allow", "match": {"hosts": ["calendar.googleapis.com", "www.googleapis.com"],
            "methods": ["GET"], "path_regex": r"^/calendar/v3/users/me/calendarList$"}},
    ]}


def probes(name):
    selected_services([name])
    if name == "gmail":
        return [("gmail.googleapis.com", "/gmail/v1/users/me/profile", {}),
                ("gmail.googleapis.com", "/gmail/v1/users/me/messages", {"maxResults": 1, "fields": "messages/id,resultSizeEstimate"})]
    if name == "drive":
        return [("www.googleapis.com", "/drive/v3/about", {"fields": "user/emailAddress"}),
                ("www.googleapis.com", "/drive/v3/files", {"pageSize": 1, "fields": "files/id"})]
    return [("www.googleapis.com", "/calendar/v3/users/me/calendarList", {"maxResults": 1, "fields": "items/id"})]


def validate_probe(name, index, payload, email):
    selected_services([name])
    if not isinstance(payload, dict):
        raise WorkflowError(502, "invalid_provider_response", "The service check returned an invalid response.")
    if name == "calendar":
        items = payload.get("items", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in items):
            raise WorkflowError(502, "invalid_provider_response", "The calendar listing returned an invalid response.")
        return  # OIDC verifies identity; calendar IDs may identify shared calendars.
    if index == 0:
        actual = payload.get("emailAddress", "") if name == "gmail" else (payload.get("user") or {}).get("emailAddress", "")
        if str(actual).lower() != email:
            raise WorkflowError(400, "identity_mismatch", "The service account does not match the requested account.")
    elif not isinstance(payload.get("messages" if name == "gmail" else "files", []), list):
        raise WorkflowError(502, "invalid_provider_response", "The service listing returned an invalid response.")


def public_operation(item):
    keys = ("id", "kind", "email", "status", "phase", "error_code", "recovery", "created_at", "updated_at",
            "promoted_at", "verified_at", "authorization_written_at", "propagation_complete_at", "consent_expires_at")
    return {key: item[key] for key in keys if key in item}


def account_projection(item):
    evidence = item.get("verifications", {})
    scopes = item.get("granted_scopes", [])
    services = [{"name": name, "authorization_status": "granted" if SCOPES_BY_SERVICE[name] in scopes else "missing",
                 "last_verification_status": evidence.get(name, {}).get("status", "not_checked"),
                 "last_verified_at": int(evidence.get(name, {}).get("verified_at", 0))}
                for name in item.get("installed_services", []) if name in SCOPES_BY_SERVICE]
    return {"email": item["email"], "connection_status": item.get("connection_status", "not_connected"), "services": services}
