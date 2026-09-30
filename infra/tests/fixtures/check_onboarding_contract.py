"""Offline DOM journey through the real owner HTTP handler and service contracts.

uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python infra/tests/fixtures/check_onboarding_contract.py
Only owner identity, Google consent, and persistence use explicit offline fixtures.
The Google page is intercepted; this test makes no AWS or Google calls.
"""
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

LAMBDA = Path(__file__).resolve().parents[2] / "lambda/google_proxy"
sys.path.insert(0, str(LAMBDA))
import onboarding
from onboarding_auth import OwnerAuth
from onboarding_google import GoogleClient
from onboarding_operations import OnboardingService
from onboarding_services import requested_scopes
from test_onboarding_lifecycle import MemoryStore

SESSION = "gogcli-proxy-contract"
PROJECT = "jayj-gogcli-full-access"
EMAIL = "jay@frunkholdings.ltd"
CALLS = []


class OwnerFixture:
    session_id = "fixture-session"

    def session(self, headers):
        return {"owner": "fixture", "session_id": self.session_id, "csrf": "fixture-csrf"}

    def check_mutation(self, headers, session):
        auth = OwnerAuth.__new__(OwnerAuth)
        auth.origin = os.environ["ONBOARDING_BASE_URL"].removesuffix("/onboarding/")
        auth.check_mutation(headers, session)


STORE = MemoryStore()
SERVICE = OnboardingService(STORE, GoogleClient(None), "https://example.invalid/google-callback")
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
    result = subprocess.run(["agent-browser", "--session", SESSION, *args],
                            text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def check(code):
    browser("eval", "(() => { " + code + "; return 'passed'; })()")


def intercept_google():
    # Installed agent-browser 0.9.0 ignores CLI --body; its daemon accepts response.
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


def latest():
    return STORE.get("ACCOUNT#" + EMAIL)["latest_operation_id"]


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/onboarding/"
    os.environ["ONBOARDING_BASE_URL"] = url
    for email in ("jay.jakosky@gmail.com", EMAIL, "treasurer@longmonttheatre.org"):
        SERVICE.save_account("fixture", email, {"projectId": PROJECT, "services": ["gmail", "drive"]})
    installed = SERVICE.account("fixture", "jay.jakosky@gmail.com")
    STORE.put({**installed, "connection_status": "connected", "credential_revision": "fixture-installed",
               "installed_services": ["gmail"], "requested_services": ["gmail"], "granted_scopes": requested_scopes(["gmail"]),
               "installed_project_id": PROJECT, "verifications": {"gmail": {"status": "succeeded", "verified_at": 1760000000}}})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        browser("open", url)
        intercept_google()
        browser("wait", "#accounts article")
        check("""
          if(document.querySelector('#confirmStep #apiConfirmations')!==document.querySelector('#apiConfirmations'))
            throw Error('API confirmations must follow Web client upload in step 4');
          if(document.querySelector('#apisStep #apiConfirmations')) throw Error('step 1 repeats step 4 review');
          if(!document.querySelector('#connectAccounts')?.disabled) throw Error('step 5 must wait for Web client and API review');
          if([...document.querySelectorAll('.connect-account')].some(button=>!button.disabled))
            throw Error('consent is enabled before Google setup');
          const cards=[...document.querySelectorAll('.account')];
          if(cards.some(card=>!card.querySelector('[data-service="calendar"]')))
            throw Error('Calendar selection unavailable');
          if(cards.some(card=>card.querySelector('[data-service="calendar"]').checked))
            throw Error('Calendar auto-expanded existing selections');
          const link=[...document.querySelectorAll('#apiLinks a')].find(link=>link.textContent.includes('Calendar'));
          if(!link || new URL(link.href).pathname!=='/apis/library/calendar-json.googleapis.com')
            throw Error('Calendar API link does not target actual Google library');
          cards.find(card=>card.dataset.email==='jay@frunkholdings.ltd').querySelector('[data-service="calendar"]').click();
          document.querySelector('#refresh').click();
        """)
        browser("wait", "200")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='jay@frunkholdings.ltd');
          if(!card.querySelector('[data-service="calendar"]').checked) throw Error('refresh lost Calendar draft');
          card.querySelector('[data-service="calendar"]').click();
          const draft=drafts.get('jay@frunkholdings.ltd');
          if(JSON.stringify(draft.services)!==JSON.stringify(['gmail','drive'])) throw Error('Calendar deselect changed Gmail/Drive');
          document.querySelector('[data-email="jay@frunkholdings.ltd"] [data-service="calendar"]').click();
        """)
        web_json = {"web": {"client_id": "actual-imported-fixture-client", "client_secret": "offline-fixture-secret",
                            "project_id": PROJECT, "redirect_uris": [SERVICE.callback_url]}}
        check("""
          document.querySelector('#clientStep').open=true;
          const files=new DataTransfer();
          files.items.add(new File([JSON.stringify(%s)],'offline-google-web.json',{type:'application/json'}));
          document.querySelector('#credentialsFile').files=files.files;
          document.querySelector('#credentialsForm').requestSubmit();
        """ % json.dumps(web_json))
        browser("wait", "200")
        if STORE.get("CLIENT#" + PROJECT)["client_id"] != "actual-imported-fixture-client":
            raise AssertionError("Web JSON did not reach the real owner import handler")
        check("""
          if(!document.querySelector('#confirmStep').open) throw Error('upload did not open API review');
          if([...document.querySelectorAll('#confirmStep #apiConfirmations input')].some(input=>input.checked))
            throw Error('upload retained unreviewed API confirmations');
          document.querySelector('#saveProject').click();
        """)
        browser("wait", "200")
        check("""
          const status=document.querySelector('#projectSaveStatus').textContent;
          if(!status.includes('Gmail') || !status.includes('Drive') || !status.includes('Calendar') || status.includes('Google setup complete'))
            throw Error('unchecked APIs lack specific next-action guidance: '+status);
          if(!document.querySelector('#connectAccounts').disabled) throw Error('unchecked APIs enabled connection');
        """)
        if any(item["path"].endswith("/setup") or item["path"].endswith("/enrollments") for item in CALLS):
            raise AssertionError("incomplete API review submitted setup or started enrollment")
        check("""
          document.querySelectorAll('#confirmStep #apiConfirmations input').forEach(input=>{if(input.dataset.service!=='calendar')input.click();});
          document.querySelector('#saveProject').click();
        """)
        browser("wait", "200")
        check("""
          if(!document.querySelector('#projectSaveStatus').textContent.includes('Calendar')) throw Error('missing Calendar review lacks named guidance');
          if(!document.querySelector('[data-email="jay@frunkholdings.ltd"] .connect-account').disabled) throw Error('missing Calendar API allows consent');
        """)
        if any(item["path"].endswith("/setup") or item["path"].endswith("/enrollments") for item in CALLS):
            raise AssertionError("missing Calendar API submitted review or consent")
        check("""
          document.querySelector('#confirmStep [data-service="calendar"]').click();
          document.querySelector('#saveProject').click();
        """)
        if any(item["path"].endswith("/enrollments") for item in CALLS):
            raise AssertionError("Google setup created an enrollment before the account consent action")
        browser("wait", "200")
        check("""
          if(!document.querySelector('#projectSaveStatus').textContent.includes('Google setup complete'))
            throw Error('saved API review does not mark Google setup complete');
          if(document.querySelector('#connectAccounts').disabled) throw Error('reviewed APIs did not enable step 5');
          document.querySelector('#connectAccounts').click();
          const first=[...document.querySelectorAll('.account .connect-account')].find(button=>!button.disabled);
          if(document.activeElement!==first || first.closest('.account').dataset.email!=='jay@frunkholdings.ltd')
            throw Error('step 5 did not focus first ready unconnected account');
        """)
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
          card.querySelector('.connect-account').click()
        """)
        browser("wait", "--url", "https://accounts.google.com/**")
        check("if(!document.body.textContent.includes('offlineGoogle')) throw Error('Google consent was not intercepted')")
        enrollment = CALLS[-1]
        if enrollment["body"]["services"] != ["gmail", "drive", "calendar"]:
            raise AssertionError("explicit Calendar selection did not reach account enrollment")
        consent_url = browser("get", "url").strip()
        consent_scopes = parse_qs(urlparse(consent_url).query)["scope"][0].split()
        if set(consent_scopes) != set(requested_scopes(["gmail", "drive", "calendar"])):
            raise AssertionError("Google consent omitted selected Calendar scope")
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
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          const card=[...document.querySelectorAll('.account')].find(card=>card.dataset.email==='treasurer@longmonttheatre.org');
          card.querySelector('[data-service="calendar"]').click();
          document.querySelector('[data-email="treasurer@longmonttheatre.org"] [data-service="calendar"]').click();
          document.querySelector('[data-email="treasurer@longmonttheatre.org"] .connect-account').click();
        """)
        browser("wait", "--url", "https://accounts.google.com/**")
        consent_scopes = parse_qs(urlparse(browser("get", "url").strip()).query)["scope"][0].split()
        if CALLS[-1]["body"]["services"] != ["gmail", "drive"] or set(consent_scopes) != set(requested_scopes(["gmail", "drive"])):
            raise AssertionError("Calendar deselection changed Gmail/Drive enrollment or consent scopes")
        if any(item["status"] >= 400 for item in CALLS):
            raise AssertionError(f"browser sent an incompatible command: {CALLS}")
        print("Passed real HTTP/service DOM contracts: Calendar opt-in, API library and review gating, draft refresh, selected consent scopes and deselection, Web JSON import, step 5 handoff, parallel checks, Continue/restart/cancellation recovery, withdrawn access preserved.")
    finally:
        browser("close")
        server.shutdown()


if __name__ == "__main__":
    main()
