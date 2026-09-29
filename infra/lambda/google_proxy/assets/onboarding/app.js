'use strict';
const $ = id => document.getElementById(id);
const apiBase = new URL('v1/', new URL('./', window.location.href));
let snapshot = null;
let emails = [];
let csrfToken = '';
let saveTimer;
let saveQueue = Promise.resolve();
let dirty = false;
let loading = false;
let actionPending = false;
let pollTimer;
const enrollmentKeys = new Map();
const recoveryNotices = {
  'owner-session-expired': 'Your owner session ended before Google consent returned. Sign in, then choose Continue Google consent for this mailbox. Your saved checklist and existing Gmail access are preserved.',
  'consent-incomplete': 'Google consent did not complete. Check the saved status beside your mailbox below, then retry Google consent. Existing Gmail access is preserved.',
};
const recovery = recoveryNotices[new URLSearchParams(window.location.search).get('recovery')];
if (recovery) { $('recoveryStatus').textContent = recovery; $('recoveryStatus').hidden = false; }

function message(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle('error', error);
}

async function request(route, method = 'GET', body, extraHeaders = {}) {
  const response = await fetch(new URL(route, apiBase), {
    method, credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(method !== 'GET' ? { 'X-CSRF-Token': csrfToken } : {}), ...extraHeaders },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (response.status === 401) {
    $('guide').hidden = true;
    message('pageStatus', 'Your owner session has ended. Sign in to resume your saved checklist.', true);
    if (data.loginUrl) {
      const login = new URL(data.loginUrl, window.location.href);
      if (login.origin === window.location.origin) { $('loginLink').href = login.href; $('loginLink').hidden = false; }
    }
    throw new Error('Your owner session has ended. Sign in to resume your saved checklist.');
  }
  if (!response.ok) throw new Error(data.detail || (typeof data.error === 'string' ? data.error : data.error?.message) || data.message || 'The request failed. Refresh status and try again.');
  return data;
}

function updateConsoleLinks() {
  document.querySelectorAll('[data-console]').forEach(link => {
    const url = new URL(link.dataset.console);
    if ($('projectId').value.trim()) url.searchParams.set('project', $('projectId').value.trim());
    link.href = url.href;
  });
}

function saveSetup() {
  clearTimeout(saveTimer);
  const body = { projectId: $('projectId').value.trim(), publishingConfirmed: $('publishingConfirmed').checked, emails: emails.map(email => email.trim()) };
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    await request('setup', 'PUT', body);
    message('saveStatus', 'Checklist saved on the server.');
  });
  return saveQueue;
}

function scheduleSave() {
  dirty = true;
  message('saveStatus', 'Saving your checklist…');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveSetup().catch(error => message('saveStatus', error.message + ' Your latest edits are not saved yet.', true)), 600);
  renderStatuses();
}

function operationFor(email) {
  return (snapshot?.operations || []).find(operation => operation.email?.toLowerCase() === email.trim().toLowerCase());
}

function busy(operation) {
  return operation && (operation.status === 'accepted' || operation.status === 'running') && operation.phase !== 'authorizing';
}

function describe(operation) {
  if (!operation) return 'Not connected. Complete steps 1–4, then connect this mailbox.';
  if (operation.status === 'succeeded' && operation.phase === 'ready') return 'Verified — Gmail profile and message-ID read passed through the signed proxy.';
  const stages = {
    authorizing: 'Waiting for Google consent. Continue consent, or start a new attempt if the previous one expired.',
    exchanging: 'Checking the Google account and saving authorization securely…',
    pending_promotion: 'Authorization saved. Preparing this account for the proxy…',
    verifying: 'Authorization saved. Waiting for the proxy credential cache to expire, then checking Gmail. This takes at least 65 seconds.',
    verification_failed: 'Authorization saved, but the signed proxy check failed. Retry the check after resolving the reported issue; Google consent is not needed.',
    failed: 'Connection did not complete. Review the reported issue, then retry Google consent.',
  };
  let text = stages[operation.phase] || 'Connection progress is saved. Refresh to check the latest status.';
  const failures = {
    identity_mismatch: 'Choose the requested Google account and approve all three permissions.',
    consent_exchange_failed: 'The Google consent exchange did not complete. Start a fresh attempt.',
    candidate_unusable: 'Google authorization could not read this mailbox. Existing access was preserved.',
    consent_denied: 'Google consent was canceled or declined. You can try again.',
    refresh_required: 'Google did not provide durable access. Start a fresh Google consent attempt.',
  };
  const detail = typeof operation.error === 'string' ? failures[operation.error] || operation.error : operation.error?.message;
  if (detail) text += ' ' + detail;
  if (operation.recovery) text += ' ' + operation.recovery;
  return text;
}

function renderAccounts() {
  $('accounts').replaceChildren();
  emails.forEach((email, index) => {
    const row = document.createElement('div'); row.className = 'account';
    const line = document.createElement('div'); line.className = 'account-row';
    const label = document.createElement('label'); label.textContent = 'Account ' + (index + 1);
    const input = document.createElement('input');
    input.type = 'email'; input.value = email; input.autocomplete = 'email'; input.placeholder = 'name@example.com';
    input.setAttribute('aria-describedby', 'account-status-' + index);
    input.addEventListener('input', () => { emails[index] = input.value; scheduleSave(); });
    const button = document.createElement('button'); button.type = 'button';
    button.addEventListener('click', () => { if (input.value.trim() && input.reportValidity()) connect(index); });
    const state = document.createElement('p'); state.id = 'account-status-' + index; state.className = 'account-status'; state.setAttribute('role', 'status');
    const reconnect = document.createElement('button'); reconnect.type = 'button'; reconnect.textContent = 'Reconnect Gmail'; reconnect.className = 'reconnect';
    reconnect.addEventListener('click', () => { if (input.reportValidity()) connect(index, true); });
    label.append(input); line.append(label, button); row.append(line, state, reconnect); $('accounts').append(row);
  });
  renderStatuses();
}

function renderStatuses() {
  [...$('accounts').children].forEach((row, index) => {
    const operation = operationFor(emails[index]);
    const state = row.querySelector('.account-status');
    state.textContent = emails[index].trim() ? describe(operation) : 'Enter an email to add this mailbox to your checklist.';
    const verified = operation?.status === 'succeeded' && operation.phase === 'ready';
    state.dataset.state = verified ? 'verified' : operation?.status || '';
    const button = row.querySelector('button');
    button.textContent = operation?.phase === 'verification_failed' ? 'Retry proxy check' : verified ? 'Reconnect Gmail' : operation?.phase === 'authorizing' ? 'Continue Google consent' : operation?.status === 'failed' ? 'Retry Google consent' : 'Connect Gmail';
    button.disabled = actionPending || !emails[index].trim() || !snapshot?.configured || !$('publishingConfirmed').checked || busy(operation);
    const reconnect = row.querySelector('.reconnect');
    reconnect.hidden = operation?.phase !== 'verification_failed' || operation.error_code !== 'identity_mismatch';
    reconnect.disabled = button.disabled;
  });
  const verified = (snapshot?.operations || []).find(operation => operation.status === 'succeeded' && operation.phase === 'ready');
  $('cliSection').hidden = !verified;
  if (verified) {
    $('cliConfig').textContent = JSON.stringify({ proxy_base_url: snapshot.proxyUrl, proxy_api_key: '<owner-provided proxy API key>', aws_profile: 'krae-jdev-dev-prd', default_account: verified.email }, null, 2);
    const quotedEmail = "'" + verified.email.replaceAll("'", "'\\''") + "'";
    $('cliCommand').textContent = 'bin/gog --account ' + quotedEmail + ' --readonly gmail search \'newer_than:7d\' --max 1 --json';
  }
}

async function connect(index, forceConsent = false) {
  actionPending = true; renderStatuses();
  try {
    await saveSetup();
    const operation = operationFor(emails[index]);
    let result;
    if (operation?.phase === 'verification_failed' && !forceConsent) {
      result = await request('operations/' + encodeURIComponent(operation.id) + '/verifications', 'POST', {});
    } else if (operation?.phase === 'authorizing' && operation.authorizationUrl) {
      result = operation;
    } else if (operation) {
      result = await request('operations/' + encodeURIComponent(operation.id) + '/attempts', 'POST', {});
    } else {
      const email = emails[index].trim();
      if (!enrollmentKeys.has(email)) enrollmentKeys.set(email, crypto.randomUUID());
      result = await request('enrollments', 'POST', { email }, { 'Idempotency-Key': enrollmentKeys.get(email) });
      enrollmentKeys.delete(email);
    }
    if (result.authorizationUrl) {
      const url = new URL(result.authorizationUrl);
      if (url.origin !== 'https://accounts.google.com') throw new Error('The server returned an unexpected Google consent address. Refresh and retry.');
      window.location.assign(url.href);
    } else await loadSetup();
  } catch (error) { message('saveStatus', error.message, true); }
  finally { actionPending = false; renderStatuses(); }
}

async function loadSetup() {
  if (loading) return;
  loading = true;
  clearTimeout(pollTimer);
  try {
    const data = await request('setup');
    snapshot = data; csrfToken = data.csrfToken;
    if (!dirty) {
      emails = data.emails.slice();
      $('projectId').value = data.projectId || '';
      $('publishingConfirmed').checked = data.publishingConfirmed === true;
      renderAccounts();
    }
    $('callbackUrl').value = data.callbackUrl;
    updateConsoleLinks(); renderStatuses();
    $('guide').hidden = false; $('loginLink').hidden = true;
    message('pageStatus', 'Owner session active. Saved progress loaded.');
    message('credentialStatus', data.configured ? 'Web application client saved on the server. Confirm publishing in step 3, then connect accounts.' : 'No Google client is saved yet. Complete steps 1–4.');
    if ((data.operations || []).some(operation => busy(operation))) pollTimer = setTimeout(loadSetup, 5000);
  } catch (error) { message('pageStatus', error.message + ($('loginLink').hidden ? ' Choose Reload saved progress to retry.' : ''), true); }
  finally { loading = false; }
}

$('projectId').addEventListener('input', () => { updateConsoleLinks(); scheduleSave(); });
$('publishingConfirmed').addEventListener('change', scheduleSave);
$('addAccount').addEventListener('click', () => { emails.push(''); renderAccounts(); scheduleSave(); $('accounts').lastElementChild.querySelector('input').focus(); });
$('refresh').addEventListener('click', async () => { try { await saveSetup(); await loadSetup(); } catch (error) { message('saveStatus', error.message, true); } });
$('reloadPage').addEventListener('click', loadSetup);
$('copyCallback').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('callbackUrl').value); message('copyStatus', 'Callback copied. Paste it into Authorized redirect URIs.'); }
  catch (_) { $('callbackUrl').focus(); $('callbackUrl').select(); message('copyStatus', 'Select and copy the address above.'); }
});
$('credentialsForm').addEventListener('submit', async event => {
  event.preventDefault();
  const file = $('credentialsFile').files[0];
  if (!file) return;
  $('importCredentials').disabled = true;
  try {
    if (file.size > 1024 * 1024) throw new Error('Choose a JSON file smaller than 1 MiB.');
    let data;
    try { data = JSON.parse(await file.text()); } catch (_) { throw new Error('This file is not valid JSON. Download the Web application client JSON from Google and try again.'); }
    if (!data || typeof data !== 'object' || !data.web || typeof data.web !== 'object' || data.installed) throw new Error('Choose a Web application client JSON, not a Desktop client. Return to step 4.');
    if (![data.web.client_id, data.web.client_secret].every(value => typeof value === 'string' && value.trim())) throw new Error('The JSON is missing the client ID or secret. Download the complete client JSON.');
    if (!Array.isArray(data.web.redirect_uris) || !data.web.redirect_uris.includes($('callbackUrl').value)) throw new Error('The JSON does not include this exact callback. Add the address in step 4 to the Web client, save it, then download its JSON again.');
    message('uploadStatus', 'Uploading the Web application client…');
    await request('oauth-client', 'PUT', data);
    await loadSetup();
    message('uploadStatus', 'Web client saved securely. Confirm publishing, then connect your first mailbox in step 5.');
  } catch (error) { message('uploadStatus', error.message, true); }
  finally { $('importCredentials').disabled = false; $('credentialsFile').value = ''; }
});
window.addEventListener('focus', loadSetup);
loadSetup();
