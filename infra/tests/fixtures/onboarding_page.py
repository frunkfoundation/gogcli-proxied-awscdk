"""Offline account-page API fixture; no Google, owner sessions, or AWS calls.

Run with: infra/.venv/bin/python infra/tests/fixtures/onboarding_page.py
Then use a named agent-browser session at http://127.0.0.1:8765/onboarding/.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

ASSETS = Path(__file__).resolve().parents[2] / "lambda/google_proxy/assets/onboarding"
PROJECT = "jayj-gogcli-full-access"
STATE = {
    "callbackUrl": "https://example.invalid/onboarding/oauth/google/callback",
    "csrfToken": "offline-fixture", "proxyUrl": "https://example.invalid/prd/",
    "defaultProjectId": PROJECT, "ownerCallerArn": "offline-owner",
    "serviceOptions": [
        {"name": "gmail", "label": "Gmail", "scope": "fixture:gmail", "description": "Read and change mail, including sending."},
        {"name": "drive", "label": "Drive", "scope": "fixture:drive", "description": "Read and change files within transfer limits."},
    ],
    "projects": [{"projectId": PROJECT, "clientConfigured": True, "clientRevision": "fixture-revision",
                  "publishingConfirmed": True, "apiConfirmed": {"gmail": True, "drive": True},
                  "recordedClientType": "desktop", "recordedClientId": "offline-client"}],
    "accounts": [],
}
for email in ("jay.jakosky@gmail.com", "jay@frunkholdings.ltd", "treasurer@longmonttheatre.org"):
    STATE["accounts"].append({"email": email, "connection_status": "not_connected", "services": [],
                              "selected_project_id": PROJECT, "requested_services": ["gmail", "drive"],
                              "owner_access_enabled": True})
STATE["accounts"][0].update({"connection_status": "connected", "installed_project_id": PROJECT,
    "services": [{"name": "gmail", "authorization_status": "authorized", "last_verification_status": "passed", "last_verified_at": 1760000000},
                 {"name": "drive", "authorization_status": "authorized", "last_verification_status": "passed", "last_verified_at": 1760000000}],
    "latest_operation": {"id": "op-fixture-failed", "status": "failed", "phase": "failed", "error_code": "identity_mismatch"}})
CALLS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, data, code=200, content_type="application/json"):
        payload = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/fixture/calls":
            return self.send(CALLS)
        if self.path == "/onboarding/v1/setup":
            return self.send(STATE)
        filename = self.path.removeprefix("/onboarding/") or "index.html"
        if filename not in ("index.html", "app.js", "style.css"):
            return self.send({}, 404)
        return self.send((ASSETS / filename).read_bytes(), content_type={"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[filename])

    def mutation(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        CALLS.append({"method": self.command, "path": self.path, "body": data})
        route = unquote(self.path).split("/")
        if "accounts" in route:
            email = route[route.index("accounts") + 1]
            account = next((item for item in STATE["accounts"] if item["email"] == email), None)
            if account is None:
                account = {"email": email, "connection_status": "not_connected", "services": [], "owner_access_enabled": True}
                STATE["accounts"].append(account)
            if self.command == "PUT" and route[-1] == email:
                if account.get("latest_operation", {}).get("phase") == "authorizing":
                    return self.send({"detail": "Finish the current account action before changing setup."}, 409)
                account.update(selected_project_id=data["projectId"], requested_services=data["services"])
                return self.send(account)
            account["latest_operation"] = {"id": "op-fixture-" + email, "kind": route[-1], "status": "running", "phase": "verifying"}
            return self.send(account["latest_operation"], 202)
        if "projects" in route:
            if data.get("clientRevision") != STATE["projects"][0]["clientRevision"]:
                return self.send({"detail": "Review the current client revision."}, 409)
            STATE["projects"][0].update(data)
        if self.path == "/onboarding/v1/setup":
            STATE.update(data)
        self.send(STATE)

    do_PUT = mutation
    do_POST = mutation
    do_DELETE = mutation


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
