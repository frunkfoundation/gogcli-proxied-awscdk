"""Offline DOM journey through the real owner HTTP handler and service contracts.

uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python infra/tests/fixtures/check_onboarding_contract.py
Only owner identity, Google consent, and persistence use explicit offline fixtures.
The Google page is intercepted; this test makes no AWS or Google calls.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

LAMBDA = Path(__file__).resolve().parents[2] / "lambda/google_proxy"
sys.path.insert(0, str(LAMBDA))
import onboarding
from onboarding_auth import OwnerAuth
from onboarding_operations import OnboardingService
from onboarding_services import requested_scopes
from test_onboarding_lifecycle import MemoryStore

SESSION = "gogcli-proxy-contract"
PROJECT = "jayj-gogcli-full-access"
EMAIL = "jay@frunkholdings.ltd"
CALLS = []


class GoogleFixture:
    def authorization_url(self, *args):
        return "https://accounts.google.com/o/oauth2/v2/auth?offline-fixture=1"


class OwnerFixture:
    session_id = "fixture-session"

    def session(self, headers):
        return {"owner": "fixture", "session_id": self.session_id, "csrf": "fixture-csrf"}

    def check_mutation(self, headers, session):
        auth = OwnerAuth.__new__(OwnerAuth)
        auth.origin = os.environ["ONBOARDING_BASE_URL"].removesuffix("/onboarding/")
        auth.check_mutation(headers, session)


STORE = MemoryStore()
SERVICE = OnboardingService(STORE, GoogleFixture(), "https://example.invalid/google-callback")
onboarding.get_service = lambda: SERVICE
onboarding.get_auth = lambda: OwnerFixture()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def request(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        event = {"path": self.path, "httpMethod": self.command, "body": raw,
                 "headers": dict(self.headers), "queryStringParameters": {}}
        result = onboarding.handler(event, None)
        if self.command != "GET":
            CALLS.append({"method": self.command, "path": self.path,
                          "body": json.loads(raw) if raw else {}, "status": result["statusCode"]})
        self.send_response(result["statusCode"])
        for name, value in result["headers"].items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(result["body"].encode())

    do_GET = request
    do_PUT = request
    do_POST = request
    do_DELETE = request


def browser(*args):
    return subprocess.run(["agent-browser", "--session", SESSION, *args],
                          text=True, capture_output=True, check=True).stdout


def check(code):
    browser("eval", "(() => { " + code + "; return 'passed'; })()")


def latest():
    return STORE.get("ACCOUNT#" + EMAIL)["latest_operation_id"]


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/onboarding/"
    os.environ["ONBOARDING_BASE_URL"] = url
    client_ref = STORE.secret_put("clients/fixture", {"client_id": "actual-imported-fixture-client"})
    STORE.put({"pk": "CLIENT#" + PROJECT, "project_id": PROJECT, "revision": "fixture-revision",
               "client_id": "actual-imported-fixture-client", "secret_ref": client_ref,
               "publishing_confirmed": True, "api_confirmed": {"gmail": True, "drive": True}})
    for email in ("jay.jakosky@gmail.com", EMAIL):
        SERVICE.save_account("fixture", email, {"projectId": PROJECT, "services": ["gmail", "drive"]})
    installed = SERVICE.account("fixture", "jay.jakosky@gmail.com")
    STORE.put({**installed, "connection_status": "connected", "credential_revision": "fixture-installed",
               "installed_services": ["gmail"], "requested_services": ["gmail"], "granted_scopes": requested_scopes(["gmail"]),
               "installed_project_id": PROJECT, "verifications": {"gmail": {"status": "succeeded", "verified_at": 1760000000}}})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        browser("network", "route", "https://accounts.google.com/**", "--body", '{"offlineGoogle":true}')
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          if(!document.querySelector('#adminInstructions').textContent.includes('actual-imported-fixture-client')) throw Error('administrator client ID does not match imported client');
          const cards=[...document.querySelectorAll('.account')];
          const installed=cards.find(card=>card.dataset.email==='jay.jakosky@gmail.com');
          if(!installed.textContent.includes('last check passed')) throw Error('real service result status misunderstood');
          installed.querySelector('button').click()
        """)
        browser("wait", "200")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay@frunkholdings.ltd');
          if(card.querySelector('button').disabled) throw Error('real A verification blocked B');
          card.querySelector('button').click()
        """)
        browser("wait", "--url", "https://accounts.google.com/**")
        first = latest()
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay@frunkholdings.ltd');
          if(card.querySelector('button').textContent!=='Continue Google consent') throw Error('reload lacks Continue action');
          card.querySelector('button').click()
        """)
        browser("wait", "200")
        if CALLS[-1]["status"] >= 400:
            raise AssertionError("Continue did not replay the accepted command key and fingerprint")
        browser("wait", "--url", "https://accounts.google.com/**")
        if latest() != first:
            raise AssertionError("Continue after reload replaced the accepted operation")
        OwnerFixture.session_id = "fixture-new-session"
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay@frunkholdings.ltd');
          if(card.querySelector('button').textContent!=='Restart Google consent') throw Error('new owner session did not require fresh consent');
          const options=card.querySelector('details'); options.open=true;
          [...options.querySelectorAll('button')].find(button=>button.textContent==='Restart Google consent').click()
        """)
        browser("wait", "--url", "https://accounts.google.com/**")
        second = latest()
        if second == first:
            raise AssertionError("explicit restart reused the previous attempt")
        op = STORE.get("OP#" + second)
        attempt = STORE.secret_get(op["attempt_ref"])
        try:
            SERVICE.callback(attempt["state"], "", OwnerFixture.session_id, "access_denied")
        except Exception as error:
            if getattr(error, "code", "") != "consent_denied":
                raise
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay@frunkholdings.ltd');
          if(!card.textContent.includes('Google consent was canceled')) throw Error('canceled consent recovery missing');
          card.querySelector('button').click()
        """)
        browser("wait", "--url", "https://accounts.google.com/**")
        if latest() == second:
            raise AssertionError("new consent after cancellation reused the failed attempt")
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay.jakosky@gmail.com');
          card.querySelector('[data-service="drive"]').click()
        """)
        account = SERVICE.account("fixture", "jay.jakosky@gmail.com")
        STORE.terminal(STORE.get("OP#" + account["active_operation"]), {"phase": "ready", "status": "succeeded"}, 1760000000)
        account = SERVICE.account("fixture", "jay.jakosky@gmail.com")
        STORE.put({**account, "desired_owner_access": False})
        check("document.querySelector('#refresh').click()")
        browser("wait", "200")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay.jakosky@gmail.com');
          const options=card.querySelector('details'); options.open=true;
          [...options.querySelectorAll('button')].find(button=>button.textContent==='Reconnect / add selected service').click()
        """)
        browser("wait", "--url", "https://accounts.google.com/**")
        if CALLS[-1]["body"]["ownerAccessEnabled"]:
            raise AssertionError("reconnect restored withdrawn caller access from an old draft")
        if any(item["status"] >= 400 for item in CALLS):
            raise AssertionError(f"browser sent an incompatible command: {CALLS}")
        print("Passed real HTTP/service DOM contracts: imported client, service evidence, parallel checks, reload/Continue same operation, new-session restart, cancellation/fresh consent, withdrawn access preserved during repair.")
    finally:
        browser("close")
        server.shutdown()


if __name__ == "__main__":
    main()
