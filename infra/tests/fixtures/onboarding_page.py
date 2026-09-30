"""Offline account-page API fixture; no Google, owner sessions, or AWS calls.

Run with: infra/.venv/bin/python infra/tests/fixtures/onboarding_page.py
Then use a named agent-browser session at http://127.0.0.1:8765/onboarding/.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

ASSETS = Path(__file__).resolve().parents[2] / "lambda/google_proxy/assets/onboarding"
PROJECT = "gogcli-proxy"
EMAILS = ("jay.jakosky@gmail.com", "jay@frunkholdings.ltd", "treasurer@longmonttheatre.org",
          "jay@longmonttheatre.org", "jay@krystaljakosky.com", "jay@marinasailing.com",
          "jay@frunkfoundation.org", "jay@kraecollective.com")
STATE = {
    "csrfToken": "offline-fixture",
    "application": {"projectId": PROJECT, "name": PROJECT, "organization": "kraecollective.com",
                    "clientConfigured": True, "apiConfirmed": {"gmail": True, "drive": True}},
    "serviceOptions": [
        {"name": "gmail", "label": "Gmail", "description": "Read, send, label, and move mail to Trash"},
        {"name": "drive", "label": "Google Drive", "description": "Read, create, change, share, and delete Drive files"},
        {"name": "calendar", "label": "Google Calendar", "description": "Read, create, change, share, and delete calendars and events"},
    ],
    "accounts": [{"email": email, "connection_status": "not_connected", "services": [],
                  "uses_current_app": False, "requested_services": ["gmail", "drive"],
                  "owner_access_enabled": True} for email in EMAILS],
}
STATE["accounts"][0].update({"connection_status": "connected",
    "owner_access_enabled": True,
    "services": [{"name": name, "authorization_status": "granted", "last_verification_status": "succeeded", "last_verified_at": 1760000000} for name in ("gmail", "drive")],
    "latest_operation": {"id": "op-fixture-failed", "kind": "enrollment", "status": "failed", "phase": "failed", "error_code": "identity_mismatch"}})
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
        filename = self.path.split("?", 1)[0].removeprefix("/onboarding/") or "index.html"
        if filename not in ("index.html", "app.js", "style.css"):
            return self.send({}, 404)
        return self.send((ASSETS / filename).read_bytes(), content_type={"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[filename])

    def mutation(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        CALLS.append({"method": self.command, "path": self.path, "body": data})
        route = unquote(self.path).split("/")
        if "accounts" not in route:
            return self.send({}, 404)
        email = route[route.index("accounts") + 1]
        account = next((item for item in STATE["accounts"] if item["email"] == email), None)
        if account is None:
            account = {"email": email, "connection_status": "not_connected", "services": [], "owner_access_enabled": True, "uses_current_app": False}
            STATE["accounts"].append(account)
        if self.command == "PUT" and route[-1] == email:
            account.update(requested_services=data["services"])
            return self.send(account)
        if route[-1] == "enrollments":
            account["requested_services"] = data["services"]
        account["latest_operation"] = {"id": "op-fixture-" + email, "kind": route[-1], "status": "running", "phase": "verifying"}
        return self.send(account["latest_operation"], 202)

    do_PUT = mutation
    do_POST = mutation
    do_DELETE = mutation


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
