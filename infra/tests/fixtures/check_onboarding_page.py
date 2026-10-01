"""Offline rendered account-page regression in an independent named browser."""
import subprocess
import threading
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
          if(old.querySelector('button').textContent!=='Reconnect' || fresh.querySelector('button').textContent!=='Connect') throw Error('primary actions wrong');
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
          if(!card.querySelector('button').disabled || !card.textContent.includes('Google Calendar') || !card.textContent.includes('Contact the operator')) throw Error('missing API not actionable');
          card.querySelector('[data-service="calendar"]').click();
          document.querySelectorAll('.account')[0].querySelector('details').open=true;
          card.querySelector('[data-service="drive"]').focus();
        """)
        check("loadSetup()")
        browser("wait", "200")
        check("""
          const card=document.querySelectorAll('.account')[1];
          if(document.activeElement!==card.querySelector('[data-service="drive"]') || !document.querySelectorAll('.account')[0].querySelector('details').open) throw Error('polling lost focus or open options');
        """)
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
          if(old.querySelector('button').textContent!=='Reconnect to add Google Calendar') throw Error('added service hidden in options');
          if(!old.querySelector('[data-service="gmail"]').disabled || !old.querySelector('[data-service="drive"]').disabled) throw Error('installed services silently removable');
          if(old.querySelectorAll('p')[2].textContent.includes('Calendar')) throw Error('draft service presented as installed');
        """)
        STATE["application"]["apiConfirmed"]["calendar"] = True
        STATE["accounts"][0]["connection_status"] = "needs_attention"
        STATE["accounts"][1]["owner_access_enabled"] = False
        refresh()
        check("""
          if(!document.querySelector('#connectedCount').textContent.startsWith('0 of 8')) throw Error('needs_attention counted complete');
          const fresh=document.querySelectorAll('.account')[1];
          if(!fresh.textContent.includes('Tool access: paused') || !fresh.textContent.includes('Tool access will remain paused')) throw Error('paused initial Connect promises enabled tools');
          fresh.querySelector('button').click();
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
        print("Passed account page: canonical count, eight saved cards, drafts/focus, service addition, readiness scope, recovery priority, paused Connect, independent accounts, callback focus, add accounts and mobile keyboard.")
    finally:
        browser("close")
        server.shutdown()


if __name__ == "__main__":
    main()
