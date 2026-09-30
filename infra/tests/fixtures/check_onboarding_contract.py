"""Offline browser journey through real handler, commands, callback and worker.

uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python infra/tests/fixtures/check_onboarding_contract.py
Google authorization navigation is intercepted. Identity, HTTP transport, owner
session and storage are explicit offline fixtures; no AWS or Google calls occur.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

LAMBDA = Path(__file__).resolve().parents[2] / "lambda/google_proxy"
sys.path.insert(0, str(LAMBDA))
import onboarding
import onboarding_worker as worker
from onboarding_auth import OwnerAuth
from onboarding_google import GoogleClient
from onboarding_operations import OnboardingService
from onboarding_services import requested_scopes
from test_onboarding_lifecycle import MemoryStore
from onboarding_page import EMAILS

SESSION = "gogcli-single-account-contract"
PROJECT = "gogcli-proxy"
OLD, EMAIL, CALENDAR = EMAILS[:3]
CALLS = []


class OwnerFixture:
    session_id = "fixture-session"

    def session(self, headers):
        return {"owner": "fixture", "session_id": self.session_id, "csrf": "fixture-csrf"}

    def check_mutation(self, headers, session):
        auth = OwnerAuth.__new__(OwnerAuth)
        auth.origin = os.environ["ONBOARDING_BASE_URL"].removesuffix("/onboarding/")
        auth.check_mutation(headers, session)


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class GoogleTransportFixture:
    """Only fixture tokens and read-only named-probe responses."""
    exchanges = 0
    fail = False
    email = EMAIL
    probes = []

    def post(self, url, data, **kwargs):
        assert url == "https://oauth2.googleapis.com/token"
        if self.fail:
            return Response({}, 503)
        if data["grant_type"] == "authorization_code":
            self.exchanges += 1
            return Response({"refresh_token": "offline-refresh", "id_token": "offline-identity", "scope": " ".join(requested_scopes(["gmail", "drive", "calendar"]))})
        return Response({"access_token": "offline-access"})

    def get(self, url, headers, **kwargs):
        email = headers.get("X-GOG-Account", self.email)
        self.probes.append((email, urlparse(url).path))
        if urlparse(url).path.endswith("profile"):
            return Response({"emailAddress": email})
        if urlparse(url).path.endswith("about"):
            return Response({"user": {"emailAddress": email}})
        return Response({"items": [], "messages": [], "files": []})


STORE = MemoryStore()
HTTP = GoogleTransportFixture()
SERVICE = OnboardingService(STORE, GoogleClient(HTTP), "https://example.invalid/google-callback")
onboarding.get_service = lambda: SERVICE
onboarding.get_auth = lambda: OwnerFixture()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def request(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        parsed = urlparse(self.path)
        event = {"path": parsed.path, "httpMethod": self.command, "body": raw,
                 "headers": dict(self.headers), "queryStringParameters": {key: value[0] for key, value in parse_qs(parsed.query).items()}}
        result = onboarding.handler(event, None)
        if self.command != "GET":
            CALLS.append({"method": self.command, "path": parsed.path,
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
    result = subprocess.run(["agent-browser", "--session", SESSION, *args], text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def check(code):
    browser("eval", "(() => { " + code + "; return 'passed'; })()")


def intercept_google():
    # agent-browser 0.9.0 ignores CLI --body; the daemon accepts response.
    directory = os.getenv("AGENT_BROWSER_SOCKET_DIR") or (str(Path(os.environ["XDG_RUNTIME_DIR"]) / "agent-browser")
                if os.getenv("XDG_RUNTIME_DIR") else str(Path.home() / ".agent-browser"))
    command = {"id": "offline-google", "action": "route", "url": "https://accounts.google.com/**",
               "response": {"body": '{"offlineGoogle":true}', "contentType": "application/json"}}
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect(str(Path(directory) / (SESSION + ".sock")))
        connection.sendall((json.dumps(command) + "\n").encode())
        result = json.loads(connection.makefile().readline())
    if not result["success"]:
        raise AssertionError(result)


def card(email):
    return 'document.querySelector(' + json.dumps('[data-email="' + email + '"]') + ')'


def reopen(url):
    browser("open", url)
    browser("wait", "#accounts article")


def consent(email, services):
    check(card(email) + ".querySelector('button').click()")
    browser("wait", "--url", "https://accounts.google.com/**")
    check("if(!document.body.textContent.includes('offlineGoogle')) throw Error('Google navigation escaped interception')")
    query = parse_qs(urlparse(browser("get", "url").strip()).query)
    assert query["client_id"] == ["offline-canonical-client"]
    assert set(query["scope"][0].split()) == set(requested_scopes(services))
    return STORE.get("ACCOUNT#" + email)["latest_operation_id"], query["state"][0]


def process(operation):
    op = STORE.get("OP#" + operation)
    HTTP.email = op["email"]
    worker.process(operation, STORE, HTTP, now=max(int(time.time()), op.get("next_attempt", 0)))


def approve(url, email, state, operation, count="0 of 8"):
    browser("open", url + "oauth/google/callback?" + urlencode({"state": state, "code": "offline-code"}))
    browser("wait", "#accounts article")
    check("if(document.activeElement.dataset.email!==" + json.dumps(email) + ") throw Error('real callback lost account focus')")
    assert STORE.get("OP#" + operation)["phase"] == "validating"
    check("if(!document.querySelector('#connectedCount').textContent.startsWith(" + json.dumps(count) + ")) throw Error('callback counted before checks')")


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/onboarding/"
    os.environ.update(ONBOARDING_BASE_URL=url, GOOGLE_PROJECT_ID=PROJECT,
        GOOGLE_CALLBACK_URL=SERVICE.callback_url, PROXY_BASE_URL="https://fixture.invalid/prd/", PROXY_API_KEY_ID="fixture",
        ONBOARDING_OWNER_CALLER_ARN="arn:aws:iam::123456789012:role/fixture-owner",
        ONBOARDING_VERIFIER_ROLE_ARN="arn:aws:iam::123456789012:role/fixture-verifier")
    SERVICE.import_client({"web": {"project_id": PROJECT, "client_id": "offline-canonical-client", "client_secret": "offline-secret", "redirect_uris": [SERVICE.callback_url]}})
    revision = STORE.get("CLIENT#" + PROJECT)["revision"]
    SERVICE.review_application({"clientRevision": revision, "apiConfirmed": {"gmail": True, "drive": True, "calendar": True}, "publishingStatus": "testing"})
    for email in EMAILS:
        SERVICE.save_account("fixture", email, {"services": ["gmail", "drive"]})
    old = SERVICE.account("fixture", OLD)
    STORE.put({**old, "desired_owner_access": False, "connection_status": "connected", "credential_revision": "offline-existing-revision",
        "installed_project_id": "fixture-prior-application", "installed_services": ["gmail", "drive"], "granted_scopes": requested_scopes(["gmail", "drive"]),
        "verifications": {name: {"status": "succeeded", "verified_at": 1760000000} for name in ["gmail", "drive"]}})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        reopen(url)
        intercept_google()
        check("if(document.querySelector('#projectId,#appSettings,#saveDefault,select,input[type=file],pre')) throw Error('removed setup controls present');if(document.querySelectorAll('.account').length!==8) throw Error('real snapshot lost roster')")
        check("if(!document.querySelector('#connectedCount').textContent.includes('0 of 8 connected to gogcli-proxy; 1 existing connection awaiting reconnection')) throw Error('real canonical count wrong')")
        check(card(EMAIL) + ".querySelector('[data-service=calendar]').click()")
        first, _ = consent(EMAIL, ["gmail", "drive", "calendar"])
        assert CALLS[-1]["body"]["ownerAccessEnabled"] is True
        assert "projectId" not in CALLS[-1]["body"]
        assert not any(call["method"] == "PUT" for call in CALLS)
        reopen(url)
        check("if(" + card(EMAIL) + ".querySelector('button').textContent!=='Continue Google consent') throw Error('reopen lost durable continuation')")
        again, _ = consent(EMAIL, ["gmail", "drive", "calendar"])
        assert first == again
        OwnerFixture.session_id = "fixture-new-session"
        reopen(url)
        check("if(" + card(EMAIL) + ".querySelector('button').textContent!=='Restart Google consent') throw Error('new session lacks restart')")
        second, state = consent(EMAIL, ["gmail", "drive", "calendar"])
        assert second != first
        browser("open", url + "oauth/google/callback?" + urlencode({"state": state, "error": "access_denied"}))
        browser("wait", "#accounts article")
        check("if(!" + card(EMAIL) + ".textContent.includes('Google consent was canceled')) throw Error('denial lacks recovery')")
        third, state = consent(EMAIL, ["gmail", "drive", "calendar"])
        approve(url, EMAIL, state, third)
        process(third)
        op = STORE.get("OP#" + third)
        STORE.put({**op, "attempts": 4})
        HTTP.fail = True
        process(third)
        HTTP.fail = False
        reopen(url)
        check("if(" + card(EMAIL) + ".querySelector('button').textContent!=='Retry connection') throw Error('saved recovery lacks priority');" + card(EMAIL) + ".querySelector('button').click()")
        browser("wait", "200")
        recovery = STORE.get("ACCOUNT#" + EMAIL)["latest_operation_id"]
        assert recovery != third and CALLS[-1]["path"].endswith("/recoveries")
        assert HTTP.exchanges == 1
        process(recovery)
        process(recovery)
        reopen(url)
        check("if(!document.querySelector('#connectedCount').textContent.startsWith('0 of 8')) throw Error('promotion counted before named checks')")
        process(recovery)
        reopen(url)
        check("if(!document.querySelector('#connectedCount').textContent.startsWith('1 of 8')) throw Error('checked canonical connection not counted')")
        check(card(EMAIL) + ".querySelector('button').click()")
        browser("wait", "200")
        verification = STORE.get("ACCOUNT#" + EMAIL)["latest_operation_id"]
        check("const options=" + card(EMAIL) + ".querySelector('details');options.open=true;const pause=[...options.querySelectorAll('button')].find(button=>button.textContent==='Pause tool access');if(pause.disabled) throw Error('checking blocked restrictive access');pause.click()")
        browser("wait", "200")
        assert SERVICE.account("fixture", EMAIL)["desired_owner_access"] is False
        assert STORE.get("OP#" + verification)["status"] == "canceled"
        process(verification)
        assert SERVICE.account("fixture", EMAIL)["desired_owner_access"] is False
        reopen(url)
        previous, state = consent(OLD, ["gmail", "drive"])
        assert CALLS[-1]["body"]["ownerAccessEnabled"] is False
        browser("open", url + "oauth/google/callback?" + urlencode({"state": state, "error": "access_denied"}))
        browser("wait", "#accounts article")
        assert SERVICE.account("fixture", OLD)["credential_revision"] == "offline-existing-revision"
        check("if(!" + card(OLD) + ".textContent.includes('existing connection still works')) throw Error('failed replacement hid installed access')")
        replacement, state = consent(OLD, ["gmail", "drive"])
        approve(url, OLD, state, replacement, "1 of 8")
        for _ in range(4):
            process(replacement)
        assert SERVICE.account("fixture", OLD)["installed_project_id"] == PROJECT
        assert SERVICE.account("fixture", OLD)["desired_owner_access"] is False
        reopen(url)
        check("if(!document.querySelector('#connectedCount').textContent.startsWith('2 of 8')) throw Error('successful prior-app replacement not counted');if(!" + card(OLD) + ".textContent.includes('Tool access: paused')) throw Error('replacement restored paused tools')")
        STORE.put({**SERVICE.account("fixture", CALENDAR), "desired_owner_access": False})
        reopen(url)
        check(card(CALENDAR) + ".querySelector('[data-service=gmail]').click();" + card(CALENDAR) + ".querySelector('[data-service=drive]').click();" + card(CALENDAR) + ".querySelector('[data-service=calendar]').click()")
        calendar_op, state = consent(CALENDAR, ["calendar"])
        approve(url, CALENDAR, state, calendar_op, "2 of 8")
        for _ in range(4):
            process(calendar_op)
        assert HTTP.probes and all(path.endswith("/calendarList") for email, path in HTTP.probes if email == CALENDAR)
        reopen(url)
        check("if(!document.querySelector('#connectedCount').textContent.startsWith('3 of 8')) throw Error('Calendar-only successful probes not counted')")
        assert CALLS[-1]["body"]["services"] == ["calendar"]
        assert CALLS[-1]["body"]["ownerAccessEnabled"] is False
        assert SERVICE.account("fixture", CALENDAR)["desired_owner_access"] is False
        assert all(call["status"] < 400 for call in CALLS), CALLS
        print("Passed real owner HTTP/browser journey: canonical client, eight records, direct Connect, Calendar scopes, continuation/restart/denial, actual callback focus, saved recovery without repeated exchange, validation/promotion/check count, paused reconnect, retained prior access, paused canonical replacement, restrictive supersession and Calendar-only probes.")
    finally:
        browser("close")
        server.shutdown()


if __name__ == "__main__":
    with patch.object(GoogleClient, "identity", lambda self, tokens, client, nonce, email, services, prior_subject=None: {"subject": "offline-" + email, "granted_scopes": requested_scopes(services)}), \
         patch.object(worker, "enqueue"), patch.object(worker, "signed_headers", lambda url, email, host, key: {"X-GOG-Account": email}), \
         patch.object(worker, "aws_client") as aws:
        aws.return_value.get_api_key.return_value = {"value": "offline-key"}
        main()
