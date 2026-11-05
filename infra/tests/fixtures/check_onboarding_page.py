"""Offline rendered account-page regression in an independent named browser."""
import subprocess
import threading
import time
from copy import deepcopy
from http.server import HTTPServer
from onboarding_page import CALLS, Handler, STATE

SESSION = "gogcli-single-account-page"


def browser(*args):
    result = subprocess.run(["agent-browser", "--session", SESSION, *args], text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def check(code):
    browser("eval", "(() => { " + code + "; return 'passed'; })()")


def refresh():
    check("document.querySelector('#refresh').click()")
    browser("wait", "200")


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/onboarding/"
    try:
        browser("open", url)
        browser("wait", "#accounts article")
        check("""
          if(document.querySelector('#projectId,#appSettings,#saveDefault,#connectAccounts,input[type=file],select,pre'))
            throw Error('account page still contains application setup or choice');
          if(document.querySelectorAll('.account').length!==8) throw Error('saved roster must show eight accounts');
          if(!document.querySelector('#connectedCount').textContent.includes('0 of 8 connected to gogcli-proxy; 1 existing connection awaiting reconnection'))
            throw Error('historical installed connection counted as canonical');
          const old=document.querySelectorAll('.account')[0], fresh=document.querySelectorAll('.account')[1];
          if(!old.textContent.includes('existing connection still works') || !old.textContent.includes('Last reconnect failed') || !old.textContent.includes('Choose jay.jakosky@gmail.com')) throw Error('replacement failure hid installed evidence or wrong-account recovery');
          if(old.querySelector('[data-action=enrollments]').textContent!=='Reconnect in this browser' || fresh.querySelector('[data-action=enrollments]').textContent!=='Connect in this browser' || fresh.querySelector('button').dataset.action!=='link') throw Error('primary actions wrong');
          if(document.querySelector('.owner-access') || document.body.textContent.includes('Connect Google only')) throw Error('Connect needs another access decision');
          if([...document.querySelectorAll('[data-service="calendar"]')].some(input=>input.checked)) throw Error('Calendar implicitly selected');
          fresh.querySelector('[data-service="calendar"]').click();
          document.querySelector('#newEmails').value='bad email'; document.querySelector('#addAccountsForm').requestSubmit();
        """)
        if CALLS:
            raise AssertionError("visiting/selecting/invalid draft made writes")
        refresh()
        check("""
          const card=document.querySelectorAll('.account')[1];
          if(!card.querySelector('[data-service="calendar"]').checked || document.querySelector('#newEmails').value!=='bad email') throw Error('refresh lost draft');
          if(![...card.querySelectorAll('.actions button')].every(button=>button.disabled) || !card.textContent.includes('Google Calendar') || !card.textContent.includes('Contact the operator')) throw Error('missing API not actionable');
          if(!document.querySelector('#updated').textContent.startsWith('Updated')) throw Error('refresh gave no visible feedback');
          card.querySelector('[data-service="calendar"]').click();
          document.querySelectorAll('.account')[0].querySelector('details').open=true;
          card.querySelector('[data-service="drive"]').focus();
          window.unchangedControl=document.querySelectorAll('.account')[2].querySelector('button');
        """)
        check("loadSetup()")
        browser("wait", "200")
        check("""
          const card=document.querySelectorAll('.account')[1];
          if(document.activeElement!==card.querySelector('[data-service="drive"]') || !document.querySelectorAll('.account')[0].querySelector('details').open) throw Error('polling lost focus or open options');
          if(!window.unchangedControl.isConnected) throw Error('unchanged poll rebuilt controls under the pointer');
        """)
        saved = deepcopy(STATE["accounts"][0])
        now = int(time.time())
        STATE["accounts"][0].update({"uses_current_app": True,
            "services": [{"name": name, "authorization_status": "granted", "last_verification_status": "not_checked", "last_verified_at": 0} for name in ("gmail", "drive")],
            "latest_operation": {"id": "op-promoted", "kind": "enrollment", "status": "running", "phase": "verifying",
                "promoted_at": now, "authorization_written_at": now, "propagation_complete_at": now + 65}})
        refresh()
        check("""
          const card=document.querySelectorAll('.account')[0], paragraphs=[...card.querySelectorAll('p')];
          if(card.textContent.includes('Connected services:') || !card.textContent.includes('Authorized services: Gmail and Google Drive')) throw Error('unchecked authorization presented as connected services');
          if(!document.querySelector('#connectedCount').textContent.startsWith('0 of 8')) throw Error('unchecked authorization counted connected');
          if(paragraphs.filter(node=>node.textContent.includes('Google approved access.')).length!==1 || !card.textContent.includes('before checking Gmail and Google Drive') || !card.textContent.includes('can start after')) throw Error('promoted check lacks one clear status and cache wait');
          if(card.textContent.includes('Takes effect by') || card.textContent.includes('not checked · not checked') || card.textContent.includes('Tool access: enabled')) throw Error('unverified check promises availability or repeats missing timestamp');
          if(card.querySelector('button').textContent!=='Checking…' || !card.querySelector('button').disabled) throw Error('busy primary repeats status');
          for(const label of ['Pause tool access','Disconnect']) if([...card.querySelectorAll('details button')].find(button=>button.textContent===label).disabled) throw Error('active check blocks restrictive action');
          for(const label of ['Check connection','Reconnect']) if(![...card.querySelectorAll('details button')].find(button=>button.textContent===label).disabled) throw Error('active check permits overlapping operation');
        """)
        STATE["accounts"][0]["latest_operation"]["propagation_complete_at"] = now - 1
        STATE["accounts"][0]["owner_access_enabled"] = False
        refresh()
        check("""
          const card=document.querySelectorAll('.account')[0];
          if(!card.textContent.includes('Checking Gmail and Google Drive through gogcli-proxy')) throw Error('expired cache wait still hides service check');
          if(!card.textContent.includes('Tool access: paused') || ![...card.querySelectorAll('details button')].find(button=>button.textContent==='Resume tool access').disabled) throw Error('active check loses paused access or enables resume');
          if([...card.querySelectorAll('details button')].find(button=>button.textContent==='Disconnect').disabled) throw Error('paused check blocks disconnect');
        """)
        STATE["accounts"][0]["owner_access_enabled"] = True
        STATE["accounts"][0]["latest_operation"].update(status="succeeded", phase="ready", verified_at=now)
        for service in STATE["accounts"][0]["services"]:
            service.update(last_verification_status="succeeded", last_verified_at=now)
        refresh()
        check("""
          const card=document.querySelectorAll('.account')[0];
          if(!card.textContent.includes('Connected services: Gmail and Google Drive') || !document.querySelector('#connectedCount').textContent.startsWith('1 of 8')) throw Error('successful checks not presented as connected');
          if(card.textContent.includes('Connection check complete.') || !card.textContent.includes('Google Drive passed ·') || card.querySelector('.badge').textContent!=='Connected') throw Error('completed status repeated or check timestamp missing');
        """)
        STATE["accounts"][0] = saved
        STATE["application"]["clientConfigured"] = False
        refresh()
        check("""
          const old=document.querySelectorAll('.account')[0], fresh=document.querySelectorAll('.account')[1];
          if(!fresh.querySelector('button').disabled) throw Error('missing client allowed fresh consent');
          if([...old.querySelectorAll('details button')].find(button=>button.textContent==='Check connection').disabled) throw Error('missing client blocked existing check');
        """)
        STATE["application"]["clientConfigured"] = True
        STATE["accounts"][0]["uses_current_app"] = True
        STATE["accounts"][0].pop("latest_operation")
        refresh()
        check("""
          const old=document.querySelectorAll('.account')[0];
          if(!document.querySelector('#connectedCount').textContent.startsWith('1 of 8')) throw Error('promoted canonical account not counted');
          old.querySelector('[data-service="calendar"]').click();
          if(!old.textContent.includes('Google Calendar will be added when you reconnect') || old.querySelector('[data-action=enrollments]').textContent!=='Reconnect in this browser') throw Error('added service hidden in options');
          if(!old.querySelector('[data-service="gmail"]').disabled || !old.querySelector('[data-service="drive"]').disabled) throw Error('installed services silently removable');
          if([...old.querySelectorAll('p')].find(node=>/^(Connected|Authorized) services:/.test(node.textContent)).textContent.includes('Calendar')) throw Error('draft service presented as installed');
        """)
        STATE["application"]["apiConfirmed"]["calendar"] = True
        STATE["accounts"][0]["connection_status"] = "needs_attention"
        STATE["accounts"][1]["owner_access_enabled"] = False
        refresh()
        check("""
          if(!document.querySelector('#connectedCount').textContent.startsWith('0 of 8')) throw Error('needs_attention counted complete');
          const fresh=document.querySelectorAll('.account')[1];
          if(!fresh.textContent.includes('Tool access: paused') || !fresh.textContent.includes('Tool access will remain paused')) throw Error('paused initial Connect promises enabled tools');
          fresh.querySelector('[data-action=enrollments]').click();
        """)
        browser("wait", "200")
        consent = CALLS[-1]
        if consent["body"] != {"services": ["gmail", "drive"], "ownerAccessEnabled": False} or not consent["path"].endswith("/enrollments"):
            raise AssertionError("Connect did not directly record services and preserved pause")
        check("if(document.querySelectorAll('.account')[2].querySelector('button').disabled) throw Error('one account blocked another')")
        STATE["accounts"][0]["recovery_available"] = True
        refresh()
        check("if(document.querySelectorAll('.account')[0].querySelector('button').textContent!=='Retry connection') throw Error('saved recovery lost priority')")
        STATE["accounts"][0]["latest_operation"] = {"id": "op-focus", "phase": "authorizing", "status": "accepted", "consent_session_matches": False}
        refresh()
        check("if(document.querySelectorAll('.account')[0].querySelector('button').textContent!=='Restart Google consent') throw Error('session mismatch lacks restart')")
        browser("open", url + "?operation=op-focus")
        browser("wait", "#accounts article")
        check("if(document.activeElement.dataset.email!=='jay.jakosky@gmail.com') throw Error('callback did not focus operation account')")
        check("document.querySelector('#newEmails').value='new@example.com, second@example.com';document.querySelector('#addAccountsForm').requestSubmit()")
        browser("wait", "200")
        check("""
          if(document.querySelectorAll('.account').length!==10 || document.querySelector('#newEmails').value) throw Error('add accounts failed');
          for (const email of ['new@example.com', 'second@example.com']) {
            const card=[...document.querySelectorAll('.account')].find(node=>node.dataset.email===email);
            const selected=[...card.querySelectorAll('[data-service]:checked')].map(input=>input.dataset.service);
            if(JSON.stringify(selected)!==JSON.stringify(['gmail','drive','calendar'])) throw Error('new account must default to all three services');
          }
        """)
        if any(call["body"].get("projectId") for call in CALLS):
            raise AssertionError("fresh command selected project")
        browser("set", "viewport", "390", "844")
        check("document.querySelectorAll('details').forEach(node=>node.open=true); if(document.documentElement.scrollWidth>innerWidth) throw Error('mobile overflow')")
        browser("press", "Tab")
        check("if(document.activeElement===document.body) throw Error('keyboard focus lost')")
        print("Passed account page: verified connection status, promotion wait, restrictive actions, canonical count, eight saved cards, drafts/focus, service addition, readiness scope, recovery priority, paused Connect, independent accounts, callback focus, add accounts and mobile keyboard.")
    finally:
        browser("close")
        server.shutdown()


if __name__ == "__main__":
    main()
