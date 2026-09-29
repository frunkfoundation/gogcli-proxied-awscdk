"""Google Web-client enrollment; network responses never enter public errors."""
import base64
import hashlib
from urllib.parse import urlencode

from credentials import _TOKEN_CACHE, mint_access_token
from onboarding_store import WorkflowError

SCOPES = ["openid", "email", "https://www.googleapis.com/auth/gmail.modify"]
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


class GoogleClient:
    def __init__(self, http):
        self.http = http

    def authorization_url(self, client, callback, state, verifier, nonce, email):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return AUTH_URI + "?" + urlencode({"client_id": client["client_id"], "redirect_uri": callback,
            "response_type": "code", "scope": " ".join(SCOPES), "state": state, "nonce": nonce,
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

    def identity(self, tokens, client, nonce, email, prior_subject=None):
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        try:
            request = Request(session=self.http)
            def bounded_request(*args, **kwargs):
                kwargs["timeout"] = (5, 15)
                return request(*args, **kwargs)
            claims = id_token.verify_oauth2_token(tokens.get("id_token", ""), bounded_request, client["client_id"])
            if claims.get("nonce") != nonce or claims.get("email_verified") is not True:
                raise ValueError()
            if str(claims.get("email", "")).lower() != email or not claims.get("sub"):
                raise ValueError()
            if prior_subject and claims["sub"] != prior_subject:
                raise ValueError()
            granted = set(str(tokens.get("scope", "")).split())
            if "https://www.googleapis.com/auth/userinfo.email" in granted:
                granted.add("email")
            if not set(SCOPES).issubset(granted):
                raise ValueError()
            return claims["sub"]
        except Exception:
            raise WorkflowError(400, "identity_mismatch", "Consent must match the saved mailbox and requested permissions.") from None

    def validate_candidate(self, email, credentials):
        try:
            _TOKEN_CACHE.pop(email, None)  # A candidate must prove a fresh refresh, even in a warm runtime.
            access = mint_access_token(email, credentials, self.http)
            headers = {"Authorization": "Bearer " + access}
            profile = self.http.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers=headers, timeout=(5, 15))
            messages = self.http.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"maxResults": 1, "fields": "messages/id,resultSizeEstimate"}, headers=headers, timeout=(5, 15))
            if profile.status_code != 200 or messages.status_code != 200:
                raise ValueError()
            if profile.json().get("emailAddress", "").lower() != email:
                raise ValueError()
            messages.json()
        except Exception:
            raise WorkflowError(400, "candidate_unusable", "Authorization could not read this mailbox; existing access is preserved.") from None
