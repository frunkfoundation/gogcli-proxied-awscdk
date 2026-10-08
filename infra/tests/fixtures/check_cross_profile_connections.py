"""Offline cross-profile browser proof using the real HTTP handler and worker.

uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python infra/tests/fixtures/check_cross_profile_connections.py
Named browser sessions have independent cookie jars. Google navigation is
intercepted and asserted; AWS clients are forbidden. ID-token cryptography is
offline, while the application's account/nonce/scope validator stays real.
"""
import json
import os
import socket
import threading
import time
from contextlib import ExitStack
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

import check_onboarding_contract as fixture
from onboarding_auth import AuthError, read_cookie
from onboarding_store import account_parameter
from google.oauth2 import id_token
from test_onboarding_links import LinkStore

fixture.STORE = LinkStore()
fixture.SERVICE.store = fixture.STORE
fixture.HTTP.close = lambda: None

OWNER, TARGET_A, TARGET_B = ("gogcli-cross-profile-" + suffix for suffix in ("owner", "target-a", "target-b"))
SESSIONS = (OWNER, TARGET_A, TARGET_B)
REQUESTS = []
CLAIMS = {}


class OwnerFixture(fixture.OwnerFixture):
    def session(self, headers):
        if read_cookie(headers, "__Host-gog-owner") != "offline-owner":
            raise AuthError(401, "login_required", "Owner fixture cookie required.")
        return super().session(headers)


class Handler(fixture.Handler):
    def request(self):
        parsed = urlparse(self.path)
        assert parsed.netloc == "", "fixture received unexpected external URL"
        assert "capability" not in parsed.query, "capability leaked into request URL"
        assert not parsed.path.startswith("/onboarding/auth/"), "target visited owner identity provider"
        REQUESTS.append({"path": parsed.path, "cookie": self.headers.get("Cookie", ""),
                         "referer": self.headers.get("Referer", "")})
        return super().request()

    do_GET = request
    do_POST = request
    do_PUT = request
    do_DELETE = request


def browser(session, *args):
    fixture.SESSION = session
    return fixture.browser(*args)


def check(session, code):
    browser(session, "eval", "(async () => { " + code + "; return 'passed'; })()")


def intercept_google(session):
    directory = os.getenv("AGENT_BROWSER_SOCKET_DIR") or (str(Path(os.environ["XDG_RUNTIME_DIR"]) / "agent-browser")
                if os.getenv("XDG_RUNTIME_DIR") else str(Path.home() / ".agent-browser"))
    for pattern, response in (("https://accounts.google.com/**", {"body": '{"offlineGoogle":true}', "contentType": "application/json"}),
                              ("https://auth.k-rae.com/**", {"body": '{"forbiddenOwnerLogin":true}', "contentType": "application/json"})):
        command = {"id": "offline-cross-profile", "action": "route", "url": pattern, "response": response}
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(10)
            connection.connect(str(Path(directory) / (session + ".sock")))
            connection.sendall((json.dumps(command) + "\n").encode())
            result = json.loads(connection.makefile().readline())
        assert result["success"], result


def reopen_owner(url, count=None):
    browser(OWNER, "open", url)
    browser(OWNER, "wait", "#accounts article")
    if count:
        check(OWNER, "if(!document.querySelector('#connectedCount').textContent.startsWith(" + json.dumps(count) + ")) throw Error('owner count wrong')")


def mint(url, email):
    reopen_owner(url)
    check(OWNER, "const card=" + fixture.card(email) + ";const details=card.querySelector('details');if(details)details.open=true;"
          "const button=card.querySelector('[data-action=link]');"
          "if(!button) throw Error('missing owner mint action');button.click()")
    selector = '[data-email="' + email + '"] .connection-url'
    browser(OWNER, "wait", selector)
    link = browser(OWNER, "get", "value", selector).strip()
    check(OWNER, "const card=" + fixture.card(email) + ",copy=card.querySelector('[data-action=copy]');navigator.clipboard.writeText=async text=>{window.copied=text};"
          "copy.click();await new Promise(done=>setTimeout(done));"
          "if(window.copied!==card.querySelector('.connection-url').value||copy.textContent!=='Copied ✓') throw Error('copy gave no feedback')")
    assert urlparse(link).fragment and not urlparse(link).query
    operation = fixture.SERVICE.account("fixture", email)["active_operation"]
    assert fixture.STORE.get("OP#" + operation)["phase"] == "awaiting_connection"
    assert urlparse(link).fragment.split('.')[0] == fixture.STORE.get("OP#" + operation)["connection_link_id"]
    return link, operation


def claim(session, link, email):
    browser(session, "open", link)
    browser(session, "wait", "#connectionStatus")
    browser(session, "wait", "200")
    op_id = fixture.SERVICE.account("fixture", email)["latest_operation_id"]
    assert fixture.STORE.get("OP#" + op_id)["phase"] in {"awaiting_connection", "authorizing"}, (
        "claim changed phase", fixture.STORE.get("OP#" + op_id)["phase"],
        [(call["path"], call["status"]) for call in fixture.CALLS[-4:]])
    check(session, "if(document.querySelector('#continueGoogle').hidden) throw Error('claim rejected: '+document.querySelector('#connectionStatus').textContent)")
    check(session, "if(location.hash) throw Error('secret fragment retained');"
          "if(document.querySelector('#connectionEmail').textContent!==" + json.dumps(email) + ") throw Error('wrong frozen account');"
          "if(localStorage.length||sessionStorage.length) throw Error('secret persisted in browser storage');"
          "if(document.querySelector('#accounts,#connectedCount')) throw Error('scoped page exposed owner dashboard')")
    intercept_google(session)


def consent(session, operation, email, services):
    browser(session, "click", "#continueGoogle")
    browser(session, "wait", "--url", "https://accounts.google.com/**")
    check(session, "if(!document.body.textContent.includes('offlineGoogle')) throw Error('Google navigation escaped interception')")
    check(session, "if(document.referrer) throw Error('Google received a referrer')")
    query = parse_qs(urlparse(browser(session, "get", "url").strip()).query)
    assert query["client_id"] == ["offline-canonical-client"]
    assert query["login_hint"] == [email]
    assert set(query["scope"][0].split()) == set(fixture.requested_scopes(services))
    assert query["code_challenge_method"] == ["S256"]
    assert query["nonce"] and query["code_challenge"]
    assert fixture.STORE.get("OP#" + operation)["phase"] == "authorizing"
    return query["state"][0]


def callback(session, url, state, operation, email):
    attempt = fixture.STORE.secret_get(fixture.STORE.get("OP#" + operation)["attempt_ref"])
    CLAIMS.update(email=email, email_verified=True, nonce=attempt["nonce"], sub="offline-" + email)
    browser(session, "open", url + "oauth/google/callback?" + urlencode({"state": state, "code": "offline-code"}))
    browser(session, "wait", "#connectionStatus")
    check(session, "if(location.search.includes('code=')||location.search.includes('state=')) throw Error('callback query retained');"
          "if(document.body.textContent.includes('forbiddenOwnerLogin')) throw Error('target visited Authentik')")


def finish(operation):
    for _ in range(4):
        fixture.process(operation)


def forbidden_owner_api(session, url):
    check(session, "const response=await fetch(" + json.dumps(url + "v1/setup") + ");"
          "if(response.status!==401) throw Error('scoped cookie granted owner dashboard')")
    cookies = json.loads(browser(session, "--json", "cookies", "get"))["data"]["cookies"]
    assert not any(cookie["name"] == "__Host-gog-owner" for cookie in cookies)
    scoped = [cookie for cookie in cookies if cookie["name"].startswith("__Host-gog-connect-")]
    assert scoped and all(cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Lax" for cookie in scoped)


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/onboarding/"
    fixture.SERVICE.callback_url = url + "oauth/google/callback"
    os.environ.update(ONBOARDING_BASE_URL=url, GOOGLE_PROJECT_ID=fixture.PROJECT,
        GOOGLE_CALLBACK_URL=fixture.SERVICE.callback_url, PROXY_BASE_URL="https://fixture.invalid/prd/", PROXY_API_KEY_ID="fixture",
        ONBOARDING_OWNER_CALLER_ARN="arn:aws:iam::123456789012:role/fixture-owner",
        ONBOARDING_VERIFIER_ROLE_ARN="arn:aws:iam::123456789012:role/fixture-verifier")
    fixture.runtime.get_auth = lambda: OwnerFixture()
    fixture.SERVICE.import_client({"web": {"project_id": fixture.PROJECT, "client_id": "offline-canonical-client",
        "client_secret": "offline-secret", "redirect_uris": [fixture.SERVICE.callback_url]}})
    fixture.SERVICE.review_application({"clientRevision": fixture.STORE.get("CLIENT#" + fixture.PROJECT)["revision"],
        "apiConfirmed": {"gmail": True, "drive": True, "calendar": True}, "publishingStatus": "testing"})
    for email in fixture.EMAILS:
        fixture.SERVICE.save_account("fixture", email, {"services": ["gmail", "drive"]})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        browser(OWNER, "open", url)
        browser(OWNER, "cookies", "set", "__Host-gog-owner", "offline-owner", "--domain", "127.0.0.1", "--path", "/", "--secure", "--httpOnly", "--sameSite", "Lax")
        link, operation = mint(url, fixture.EMAIL)
        reopen_owner(url)
        check(OWNER, "const card=" + fixture.card(fixture.EMAIL) + ";if(card.querySelector('.connection-url')||card.querySelectorAll('[data-action=link]').length!==1||!card.textContent.includes('Links are shown only when created')) throw Error('reloaded link lacks one clear replacement action')")
        claim(TARGET_A, link, fixture.EMAIL)
        binding = fixture.STORE.get("OP#" + operation)["session_hash"]
        claim(TARGET_A, link, fixture.EMAIL)
        assert fixture.STORE.get("OP#" + operation)["session_hash"] == binding
        forbidden_owner_api(TARGET_A, url)
        browser(TARGET_B, "open", link)
        browser(TARGET_B, "wait", "#connectionStatus")
        assert fixture.STORE.get("OP#" + operation)["phase"] == "awaiting_connection"
        state = consent(TARGET_A, operation, fixture.EMAIL, ["gmail", "drive"])
        browser(TARGET_B, "open", url + "oauth/google/callback?" + urlencode({"state": state, "code": "offline-code"}))
        assert fixture.HTTP.exchanges == 0, "wrong profile exchanged code"
        assert fixture.STORE.get("OP#" + operation)["phase"] == "authorizing"
        browser(TARGET_A, "open", url + "connect/result?" + urlencode({"operation": operation}))
        browser(TARGET_A, "wait", "#continueGoogle")
        assert state == consent(TARGET_A, operation, fixture.EMAIL, ["gmail", "drive"])
        callback(TARGET_A, url, state, operation, fixture.EMAIL)
        assert fixture.STORE.get("OP#" + operation)["phase"] == "validating"
        callback(TARGET_A, url, state, operation, fixture.EMAIL)
        assert fixture.HTTP.exchanges == 1, "callback replay repeated exchange"
        reopen_owner(url, "0 of 8")
        finish(operation)
        assert fixture.STORE.get("OP#" + operation)["phase"] == "ready"
        reopen_owner(url, "1 of 8")
        forbidden_owner_api(TARGET_A, url)
        # Wrong identity through the shared validator must preserve installed credentials.
        saved = fixture.STORE.optional_secret(account_parameter(fixture.STORE.prefix, fixture.EMAIL))
        bad_link, bad_operation = mint(url, fixture.EMAIL)
        claim(TARGET_A, bad_link, fixture.EMAIL)
        bad_state = consent(TARGET_A, bad_operation, fixture.EMAIL, ["gmail", "drive"])
        callback(TARGET_A, url, bad_state, bad_operation, fixture.EMAILS[4])
        fixture.process(bad_operation)
        assert fixture.STORE.get("OP#" + bad_operation)["error_code"] == "identity_mismatch"
        assert fixture.STORE.optional_secret(account_parameter(fixture.STORE.prefix, fixture.EMAIL)) == saved
        second_link, second_operation = mint(url, fixture.EMAILS[4])
        claim(TARGET_B, second_link, fixture.EMAILS[4])
        second_state = consent(TARGET_B, second_operation, fixture.EMAILS[4], ["gmail", "drive"])
        callback(TARGET_B, url, second_state, second_operation, fixture.EMAILS[4])
        finish(second_operation)
        reopen_owner(url, "2 of 8")
        forbidden_owner_api(TARGET_B, url)
        check(TARGET_A, "const response=await fetch(" + json.dumps(url) + "+'connection/v1/operations/' + "
              + json.dumps(second_operation) + ");if(![401,404].includes(response.status)) throw Error('target read another operation')")
        # Pause invalidates a waiting link; disconnect fences an already-started attempt.
        paused_link, paused_operation = mint(url, fixture.EMAILS[2])
        fixture.SERVICE.access("fixture", fixture.EMAILS[2], False)
        browser(TARGET_B, "open", paused_link)
        browser(TARGET_B, "wait", "#connectionStatus")
        assert fixture.STORE.get("OP#" + paused_operation)["phase"] == "canceled"
        disconnected_link, disconnected_operation = mint(url, fixture.EMAILS[3])
        claim(TARGET_B, disconnected_link, fixture.EMAILS[3])
        disconnected_state = consent(TARGET_B, disconnected_operation, fixture.EMAILS[3], ["gmail", "drive"])
        fixture.SERVICE.disconnect("fixture", fixture.EMAILS[3])
        exchanges = fixture.HTTP.exchanges
        callback(TARGET_B, url, disconnected_state, disconnected_operation, fixture.EMAILS[3])
        assert fixture.HTTP.exchanges == exchanges
        assert fixture.STORE.get("OP#" + disconnected_operation)["phase"] == "canceled"
        assert all("#" not in request["referer"] for request in REQUESTS)
        print("Passed offline cross-profile browser proof: owner mint, scrubbed fragment, isolated scoped cookies, canonical Google identity/scopes, wrong binding, replay, continuation, durable worker checks/count, wrong-account preservation, pause/disconnect fences; no AWS or real Google calls.")
    finally:
        for session in SESSIONS:
            browser(session, "close")
        server.shutdown()


if __name__ == "__main__":
    with ExitStack() as patches:
        patches.enter_context(patch("boto3.client", side_effect=AssertionError("real AWS client forbidden")))
        patches.enter_context(patch("boto3.resource", side_effect=AssertionError("real AWS resource forbidden")))
        patches.enter_context(patch.object(id_token, "verify_oauth2_token", side_effect=lambda *args, **kwargs: dict(CLAIMS)))
        patches.enter_context(patch.object(fixture.worker, "enqueue"))
        patches.enter_context(patch.object(fixture.worker, "signed_headers", lambda url, email, host, key: {"X-GOG-Account": email}))
        aws = patches.enter_context(patch.object(fixture.worker, "aws_client"))
        aws.return_value.get_api_key.return_value = {"value": "offline-key"}
        main()
