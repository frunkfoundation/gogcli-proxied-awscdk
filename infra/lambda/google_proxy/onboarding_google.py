"""Hosted Google consent and bounded checks for the selected services."""
import base64
import hashlib
import time
from urllib.parse import urlencode

from onboarding_services import normalized_scopes, probes, requested_scopes, validate_probe
from onboarding_store import WorkflowError

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"


def validate_web_client(document, callback):
    web = document.get("web") if isinstance(document, dict) else None
    if not isinstance(web, dict) or any(not isinstance(web.get(key), str) or not web[key].strip() for key in ("client_id", "client_secret")):
        raise WorkflowError(400, "invalid_client", "Upload a Google Web application client JSON file.")
    if not isinstance(web.get("redirect_uris"), list) or callback not in web["redirect_uris"]:
        raise WorkflowError(400, "callback_mismatch", "Register the exact hosted callback in this Web client.")
    if web.get("token_uri", TOKEN_URI) != TOKEN_URI or web.get("auth_uri", AUTH_URI) != AUTH_URI:
        raise WorkflowError(400, "invalid_client", "Use Google's standard authorization and token endpoints.")
    if not isinstance(web.get("project_id"), str) or not web["project_id"].strip():
        raise WorkflowError(400, "invalid_client", "The Web client must identify its Google project.")
    return {key: web[key] for key in ("client_id", "client_secret", "project_id")}


def check_response(response, proxy=False):
    status = response.status_code
    if status == 200:
        return
    reason = ""
    try:
        payload = response.json()
        error = payload.get("error", {})
        if isinstance(error, dict):
            errors = error.get("errors", [])
            reason = str(errors[0].get("reason", "")) if errors else str(error.get("status", ""))
        elif error in {"invalid_grant", "refresh_revoked"}:
            reason = "invalid_grant"
    except (ValueError, AttributeError, TypeError, IndexError):
        pass
    if reason == "invalid_grant":
        code, detail = "refresh_revoked", "Google refresh authorization is unavailable; reconnect this account."
    elif reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
        code, detail = "api_disabled", "Enable this service API in the selected Google project, then retry."
    elif reason in {"domainPolicy", "admin_policy_enforced"}:
        code, detail = "workspace_restricted", "The Google Workspace administrator must approve this application."
    elif reason in {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        code, detail = "missing_service_permission", "Google did not grant the selected service permission."
    elif proxy and status in {401, 403}:
        code, detail = "permission_blocked", "The named proxy verifier cannot access this service; review its policy."
    elif status == 401:
        code, detail = "authorization_unusable", "Google authorization is unavailable; reconnect this account."
    elif status == 429 or status >= 500:
        code, detail = "provider_unavailable", "The service is temporarily unavailable; retry the saved authorization."
    else:
        code, detail = "service_check_failed", "The selected service could not be checked; review its setup and retry."
    raise WorkflowError(503 if status == 429 or status >= 500 else 400, code, detail)


class GoogleClient:
    def __init__(self, http):
        self.http = http

    def authorization_url(self, client, callback, state, verifier, nonce, email, services):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return AUTH_URI + "?" + urlencode({"client_id": client["client_id"], "redirect_uri": callback,
            "response_type": "code", "scope": " ".join(requested_scopes(services)), "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
            "prompt": "consent", "login_hint": email})

    def exchange(self, client, callback, code, verifier):
        try:
            response = self.http.post(TOKEN_URI, data={"grant_type": "authorization_code", "code": code,
                "client_id": client["client_id"], "client_secret": client["client_secret"],
                "redirect_uri": callback, "code_verifier": verifier}, timeout=(5, 15))
            if response.status_code != 200:
                raise ValueError()
            return response.json()
        except Exception:
            raise WorkflowError(400, "consent_exchange_failed", "Start a fresh consent attempt.") from None

    def identity(self, tokens, client, nonce, email, services, prior_subject=None):
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        try:
            request = Request(session=self.http)
            def bounded_request(*args, **kwargs):
                kwargs["timeout"] = (2, 3)
                return request(*args, **kwargs)
            claims = id_token.verify_oauth2_token(tokens.get("id_token", ""), bounded_request, client["client_id"])
            if claims.get("nonce") != nonce or claims.get("email_verified") is not True or not claims.get("sub"):
                raise ValueError()
        except Exception:
            raise WorkflowError(400, "invalid_identity", "Google account identity could not be verified; restart consent.") from None
        if str(claims.get("email", "")).lower() != email or (prior_subject and claims["sub"] != prior_subject):
            raise WorkflowError(400, "identity_mismatch", "Choose the exact requested Google account.")
        granted = normalized_scopes(tokens.get("scope", ""))
        if not set(requested_scopes(services)).issubset(granted):
            raise WorkflowError(400, "missing_service_permission", "Approve every selected service permission in Google consent.")
        return {"subject": claims["sub"], "granted_scopes": granted}

    def validate_candidate(self, email, credentials, services):
        # Candidate proof intentionally refreshes without the proxy's warm token cache.
        deadline = time.monotonic() + 25
        response = self.http.post(TOKEN_URI, data={"grant_type": "refresh_token",
            "client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"], "scope": " ".join(credentials["scopes"])}, timeout=(2, 4))
        check_response(response)
        access = response.json().get("access_token")
        if not isinstance(access, str) or not access:
            raise WorkflowError(502, "refresh_unavailable", "Google did not return usable refreshed authorization.")
        for name in services:
            for index, (host, path, query) in enumerate(probes(name)):
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    raise WorkflowError(503, "provider_unavailable", "Service verification exceeded its bounded deadline; retry saved authorization.")
                response = self.http.get("https://" + host + path, params=query,
                    headers={"Authorization": "Bearer " + access}, timeout=(min(1, remaining / 2), min(3, remaining / 2)))
                check_response(response)
                validate_probe(name, index, response.json(), email)
