"""Run account-page interaction regressions in a named, offline browser session.

infra/.venv/bin/python infra/tests/fixtures/check_onboarding_page.py
Requires the installed agent-browser CLI; creates no external side effects.
"""
import subprocess
import threading
from http.server import HTTPServer

from onboarding_page import CALLS, Handler, PROJECT, STATE

SESSION = "gogcli-proxy-fixture"


def browser(*args):
    result = subprocess.run(["agent-browser", "--session", SESSION, *args],
                            text=True, capture_output=True, check=True)
    return result.stdout


def check(code):
    browser("eval", "(() => { " + code + "; return 'passed'; })()")


def main():
    STATE["projects"][0]["publishingConfirmed"] = False
    STATE["projects"].append({"projectId": "another-project", "clientRevision": "other-revision"})
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/onboarding/"
    try:
        browser("open", url)
        browser("wait", "#accounts article")
        check("if(document.querySelectorAll('.account')[1].querySelector('button').textContent==='Prepare Google setup') throw Error('Testing project was blocked from consent')")
        check("""
          const connected=document.querySelectorAll('.account')[0], fresh=document.querySelectorAll('.account')[1];
          if(document.querySelector('#appSettings')?.open!==false || document.querySelector('#saveDefault').textContent!=='Use this app for all accounts')
            throw Error('advanced application settings or shared app action is unclear');
          if([...fresh.querySelectorAll('details button')].some(button=>button.textContent==='Google setup'))
            throw Error('account options repeat shared Google setup');
          if(fresh.querySelector('a[href="https://myaccount.google.com/connections"]'))
            throw Error('unconnected account offers Google revocation');
          if(!connected.querySelector('a[href="https://myaccount.google.com/connections"]'))
            throw Error('connected account lost Google revocation');
          if(!(document.querySelector('#projectGuide').compareDocumentPosition(document.querySelector('#accounts')) & Node.DOCUMENT_POSITION_FOLLOWING))
            throw Error('shared setup must precede account connections');
          if(!document.querySelector('#confirmStep #apiConfirmations') || document.querySelector('#apisStep #apiConfirmations'))
            throw Error('review must follow client upload');
          if(document.querySelector('#connectAccounts').disabled) throw Error('saved review does not enable step 5');
          document.querySelector('#connectAccounts').click();
          if(document.activeElement!==fresh.querySelector('.connect-account'))
            throw Error('step 5 lost keyboard handoff to ready account');
          if([...document.querySelectorAll('.account [data-service="calendar"]')].some(input=>input.checked))
            throw Error('existing Gmail/Drive selections auto-expanded to Calendar');
          const calendarAPI=[...document.querySelectorAll('#apiLinks a')].find(link=>link.textContent.includes('Calendar'));
          if(new URL(calendarAPI.href).pathname!=='/apis/library/calendar-json.googleapis.com')
            throw Error('Calendar API library link is incorrect');
          fresh.querySelector('[data-service="calendar"]').click();
          document.querySelector('#refresh').click();
        """)
        browser("wait", "200")
        check("""
          const fresh=document.querySelectorAll('.account')[1];
          if(!fresh.querySelector('[data-service="calendar"]').checked) throw Error('refresh lost Calendar draft');
          if(!fresh.querySelector('.connect-account').disabled || !fresh.textContent.includes('Calendar'))
            throw Error('unreviewed Calendar did not block consent with guidance');
          fresh.querySelector('[data-service="calendar"]').click();
          if(JSON.stringify(drafts.get(fresh.dataset.email).services)!==JSON.stringify(['gmail','drive']))
            throw Error('Calendar deselect changed existing Gmail/Drive selection');
          if(document.querySelectorAll('.account')[1].querySelector('.connect-account').disabled)
            throw Error('Calendar deselect did not restore Gmail/Drive readiness');
        """)
        STATE["projects"][0]["clientConfigured"] = False
        check("document.querySelector('#refresh').click()")
        browser("wait", "200")
        check("""
          const fresh=document.querySelectorAll('.account')[1];
          if(!fresh.querySelector('button').disabled || !fresh.textContent.includes('Complete Google setup above'))
            throw Error('unprepared account should wait for shared setup');
          if(!document.querySelector('#connectAccounts').disabled) throw Error('step 5 bypasses missing Web client');
          if(!fresh.querySelector('details select')) throw Error('advanced project selection disappeared');
        """)
        STATE["projects"][0]["clientConfigured"] = True
        STATE["projects"][0]["publishingConfirmed"] = True
        check("document.querySelector('#refresh').click()")
        browser("wait", "200")
        check(f"""
          const app=document.querySelector('#appName'), client=document.querySelector('#webClientName');
          if(!app?.readOnly || app.value!=={PROJECT!r} || !client?.readOnly || client.value!=={(PROJECT + ' web client')!r})
            throw Error('required Google names lack project-specific suggestions');
          if(!document.querySelector('#clientStep').textContent.includes('Web application') ||
             !document.querySelector('#clientStep').textContent.includes('Authorized JavaScript origins') ||
             !document.querySelector('#audienceStep').textContent.includes('User support email') ||
             !document.querySelector('#audienceStep').textContent.includes('Developer contact information') ||
             !document.querySelector('#serviceDescriptions').textContent.includes('openid'))
            throw Error('Google setup fields and values are missing');
          document.querySelector('#projectId').value='another-project';
          document.querySelector('#projectId').dispatchEvent(new Event('change'));
          if(client.value!=='another-project web client' || app.value!=='another-project')
            throw Error('suggested names did not follow the selected project');
          if(document.querySelector('#callbackUrl').value!=={STATE['callbackUrl']!r})
            throw Error('project choice changed the registered callback');
          Object.defineProperty(navigator, 'clipboard', {{configurable:true, value:{{writeText:async value=>{{window.fixtureCopied=value;}}}}}});
          document.querySelector('#copyWebClientName').click()
        """)
        browser("wait", "100")
        check(f"""
          if(window.fixtureCopied!=='another-project web client') throw Error('copy used a stale suggested name');
          document.querySelector('#projectId').value={PROJECT!r};
          document.querySelector('#projectId').dispatchEvent(new Event('change'))
        """)
        check("""
          document.querySelector('#clientStep').open=true;
          document.querySelectorAll('.account')[0].querySelector('details').open=true;
          window.dispatchEvent(new Event('focus'))
        """)
        browser("wait", "200")
        check("""
          if(!document.querySelector('#clientStep').open || !document.querySelectorAll('.account')[0].querySelector('details').open)
            throw Error('returning to the page collapsed the section in use')
        """)
        check("""
          const cards=document.querySelectorAll('.account');
          if(cards.length!==3) throw Error('expected three saved accounts, no blank rows');
          if(!cards[0].textContent.includes('Status: connected') ||
             !cards[0].textContent.includes('Choose this exact Google account'))
            throw Error('failed replacement hid installed access or recovery');
          if(cards[0].querySelector('button').textContent!=='Check saved connection')
            throw Error('connected primary action must check saved access');
          const access=cards[1].querySelector('.owner-access');
          if(!access.checked) throw Error('new account owner access must be explicit and checked');
          document.querySelector('#newEmails').value='not an email';
          document.querySelector('#addAccountsForm').requestSubmit()
        """)
        if CALLS:
            raise AssertionError("invalid draft was sent to the server")
        check("""
          document.querySelectorAll('.account')[1].querySelectorAll('fieldset input')[1].click();
          document.querySelector('#publishingConfirmed').click();
          document.querySelector('#refresh').click()
        """)
        browser("wait", "200")
        check("""
          if(document.querySelector('#newEmails').value!=='not an email') throw Error('draft lost on refresh');
          if(document.querySelectorAll('.account')[1].querySelectorAll('fieldset input')[1].checked)
            throw Error('service draft lost on refresh');
          if(document.querySelector('#publishingConfirmed').checked) throw Error('project draft lost on refresh');
          document.querySelectorAll('.account')[0].querySelector('button').click()
        """)
        browser("wait", "200")
        STATE["projects"][0]["clientRevision"] = "fixture-replaced-revision"
        check("document.querySelector('#refresh').click()")
        browser("wait", "200")
        check("document.querySelector('#saveProject').click()")
        browser("wait", "200")
        check("if(!document.querySelector('#projectSaveStatus').textContent.includes('current client revision')) throw Error('stale confirmation was applied to replacement client'); document.querySelector('#projectId').dispatchEvent(new Event('change'))")
        check("""
          if(document.querySelectorAll('.account')[1].querySelector('button').disabled)
            throw Error('checking A blocked B');
          document.querySelectorAll('.account')[1].querySelector('button').click()
        """)
        browser("wait", "200")
        enrollment = next(item for item in CALLS if item["path"].endswith("/enrollments"))
        if enrollment["body"]["services"] != ["gmail"] or not enrollment["body"]["ownerAccessEnabled"]:
            raise AssertionError("selected services and explicit access were not saved before consent")
        check("""
          if(document.querySelectorAll('.account')[2].querySelector('button').disabled)
            throw Error('checking B blocked C');
          document.querySelector('#confirmStep').open=true;
          document.querySelector('#saveProject').click()
        """)
        browser("wait", "200")
        check("""
          if(projectDirty) throw Error('successful save left project dirty');
          document.querySelector('#newEmails').value='four@example.com, five@example.com';
          document.querySelector('#addAccountsForm').requestSubmit()
        """)
        browser("wait", "200")
        check("if(document.querySelectorAll('.account').length!==5 || document.querySelector('#newEmails').value) throw Error('pasted accounts were not saved and draft reset')")
        added = [account for account in STATE["accounts"] if account["email"] in ("four@example.com", "five@example.com")]
        if any(account["requested_services"] != ["gmail", "drive"] for account in added):
            raise AssertionError("adding accounts enabled Calendar without an explicit selection")
        STATE["accounts"][0]["latest_operation"] = {"id": "op-waiting", "phase": "authorizing", "status": "accepted"}
        check("document.querySelector('#refresh').click()")
        browser("wait", "200")
        check("if(document.querySelectorAll('.account')[0].querySelector('button').textContent!=='Restart Google consent') throw Error('connected account cannot restart its waiting consent'); document.querySelectorAll('.account')[0].querySelector('button').click()")
        browser("wait", "200")
        restarted = CALLS[-1]
        if restarted["method"] != "POST" or not restarted["path"].endswith("/enrollments") or not restarted["body"].get("restart"):
            raise AssertionError("restart did not use the account-level explicit restart command")
        if any(item["method"] == "PUT" and item["path"].endswith("jay.jakosky%40gmail.com") for item in CALLS):
            raise AssertionError("restart submitted competing account metadata")

        browser("open", url)
        browser("wait", "#accounts article")
        check("if(!document.querySelectorAll('.account')[1].textContent.includes('Checking each selected service')) throw Error('reopen lost durable progress')")
        for account in STATE["accounts"]:
            account.pop("latest_operation", None)
        STATE["projects"][1].update(clientConfigured=True, apiConfirmed={"gmail": True, "drive": True, "calendar": True})
        original_services = {account["email"]: account["requested_services"][:] for account in STATE["accounts"]}
        browser("open", url)
        browser("wait", "#accounts article")
        check("document.querySelector('#projectId').value='another-project'; document.querySelector('#projectId').dispatchEvent(new Event('change')); document.querySelector('#saveDefault').click()")
        browser("wait", "500")
        if any(account["selected_project_id"] != "another-project" or account["requested_services"] != original_services[account["email"]] for account in STATE["accounts"]):
            raise AssertionError("shared app action did not preserve every saved service selection")
        if STATE["accounts"][0]["installed_project_id"] != PROJECT:
            raise AssertionError("shared app choice changed installed credentials")
        STATE["accounts"][0]["recovery_available"] = True
        browser("open", url)
        browser("wait", "#accounts article")
        check("if(document.querySelectorAll('.account')[0].querySelector('button').textContent!=='Retry saved authorization') throw Error('new app selection hid installed authorization recovery')")
        STATE["accounts"][0]["recovery_available"] = False
        check("document.querySelector('#refresh').click()")
        browser("wait", "200")
        check("""
          const primary=document.querySelectorAll('.account')[0].querySelector('.connect-account');
          if(primary?.textContent!=='Connect with selected app') throw Error('legacy installed account hides new-app consent');
          document.querySelector('#connectAccounts').click();
          if(document.activeElement!==primary) throw Error('handoff skipped legacy installed account new-app consent');
        """)
        browser("set", "viewport", "390", "844")
        check("document.querySelectorAll('#projectGuide details').forEach(step=>step.open=true); if(document.documentElement.scrollWidth>innerWidth) throw Error('mobile horizontal overflow')")
        browser("press", "Tab")
        check("if(document.activeElement===document.body) throw Error('keyboard focus missing')")
        print("Passed shared-app page contracts: compact application settings, all-account service preservation, legacy credentials retained, visible new-app consent and keyboard handoff.")
        print("Passed: required Google fields, open sections kept on refocus, project-specific suggested names and copy, stable callback, invalid drafts, independent refresh, reset after save, installed/attempt distinction, selected services/access, parallel accounts, pasted emails, durable reopen, expanded mobile steps, keyboard and consent restart checks.")
    finally:
        browser("close")
        server.shutdown()


if __name__ == "__main__":
    main()
