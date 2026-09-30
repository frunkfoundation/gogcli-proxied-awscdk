'use strict';
const $ = id => document.getElementById(id);
const apiBase = new URL('v1/', new URL('./', location.href));
let snapshot, csrfToken = '', loading = false, pollTimer;
const drafts = new Map(), pending = new Set(), keys = new Map();
let projectDirty = false, reviewedProjectRevision = '', reviewedServices;
function message(id, text, error = false) { $(id).textContent = text; $(id).classList.toggle('error', error); }
function element(tag, text, parent) { const node = document.createElement(tag); if (text) node.textContent = text; if (parent) parent.append(node); return node; }
async function request(route, method = 'GET', body, headers = {}) {
  const response = await fetch(new URL(route, apiBase), { method, credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(method !== 'GET' ? { 'X-CSRF-Token': csrfToken } : {}), ...headers },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const data = response.status === 204 ? {} : await response.json();
  if (response.status === 401) {
    $('guide').hidden = true;
    if (data.loginUrl) { const url = new URL(data.loginUrl, location.href); if (url.origin === location.origin) { $('loginLink').href = url.href; $('loginLink').hidden = false; } }
    throw new Error('Your session ended. Sign in again.');
  }
  if (!response.ok) throw new Error(data.detail || data.error?.message || data.message || 'Request failed. Try again.');
  return data;
}
const project = () => snapshot.projects.find(item => item.projectId === $('projectId').value);
const accountRoute = email => 'accounts/' + encodeURIComponent(email);
const canContinue = operation => operation?.phase === 'authorizing' && operation.consent_session_matches === true && operation.continuation_key && operation.continuation_request && operation.consent_expires_at * 1000 > Date.now();
const active = operation => operation && ['accepted', 'running', 'applying'].includes(operation.status);
const time = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString() : 'not checked';
function choices(select, selected) {
  snapshot.projects.forEach(item => { const option = element('option', item.projectId, select); option.value = item.projectId; }); select.value = selected;
}
function renderProject() {
  const saved = project(); if (!saved) return; reviewedProjectRevision = saved.clientRevision;
  $('appName').value = saved.projectId; $('webClientName').value = saved.projectId + ' web client';
  $('testUserEmails').textContent = snapshot.accounts.map(account => account.email).join(', ');
  message('credentialStatus', !saved.clientConfigured ? 'Complete these steps before connecting accounts.' : 'Web client uploaded. Confirm selected APIs, save settings, then connect.');
  $('publishingConfirmed').checked = saved.publishingConfirmed === true;
  $('apiLinks').replaceChildren(); $('apiConfirmations').replaceChildren(); $('serviceDescriptions').replaceChildren();
  snapshot.serviceOptions.forEach(service => {
    const link = element('a', 'Open ' + service.label + ' API ↗', $('apiLinks')); link.dataset.console = 'https://console.cloud.google.com/apis/library/' + service.name + '.googleapis.com'; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const label = element('label', '', $('apiConfirmations')); label.className = 'check';
    const input = element('input', '', label); input.type = 'checkbox'; input.dataset.service = service.name; input.checked = saved.apiConfirmed?.[service.name] === true;
    label.append(document.createTextNode(service.label + ' API is enabled')); input.addEventListener('change', markProjectDirty);
    element('li', service.scope + ' (' + service.label + ')', $('serviceDescriptions'));
  });
  element('li', 'openid, email', $('serviceDescriptions'));
  document.querySelectorAll('[data-console]').forEach(link => { const url = new URL(link.dataset.console); url.searchParams.set('project', saved.projectId); link.href = url.href; });
  $('saveProject').disabled = !saved.clientConfigured;
  $('adminInstructions').textContent = 'Please allow Google OAuth client ' + (saved.clientId || '(upload the client JSON first)') + ' in project ' + saved.projectId + ' with scopes: ' + snapshot.serviceOptions.filter(service => !reviewedServices || reviewedServices.includes(service.name)).map(service => service.scope).join(', ') + ', openid, email.';
  projectDirty = false;
}
function openNextStep() { const saved = project(); $(saved?.clientConfigured ? 'confirmStep' : 'apisStep').open = true; }
function markProjectDirty() { projectDirty = true; message('projectSaveStatus', 'Unsaved changes.'); }
function draftFor(account) {
  if (!drafts.has(account.email)) drafts.set(account.email, { projectId: account.selected_project_id || snapshot.defaultProjectId,
    services: [...new Set([...(account.requested_services || ['gmail', 'drive']), ...(account.services || []).map(service => service.name)])], ownerAccessEnabled: account.owner_access_enabled !== false, dirty: false });
  return drafts.get(account.email);
}
function operationText(operation) {
  const phases = { preparing_consent: 'Preparing Google consent…', preparing_recovery: 'Resuming saved authorization…', checking_candidate: 'Checking authorization…', applying_access: 'Applying access change…', removing: 'Disconnecting…', propagating: 'Access change saved.', disconnected: 'Disconnected.', failed: 'Failed.', canceled: 'Canceled.', authorizing: 'Waiting for Google consent.', validating: 'Checking permissions…', exchanging: 'Receiving Google authorization…', pending_promotion: 'Saving connection…', verifying: 'Checking each selected service (1–2 minutes)…', applying: 'Removing access…', disconnecting: 'Disconnecting…', ready: 'Done.' };
  const failures = { identity_mismatch: 'Choose this exact Google account when restarting consent.', missing_service_permission: 'Approve every selected service when restarting consent.', refresh_required: 'Reconnect this account.', broad_allowlist_requires_operator: 'An operator must narrow the caller allowlist before this account can be removed.', consent_expired: 'Consent expired. Restart Google consent.', project_setup_required: 'Finish Google setup below.', managed_policy_conflict: 'An operator must reconcile this account’s service policy before enrollment can proceed.', consent_denied: 'Google consent was canceled.', provider_unavailable: 'The service check failed. Try again.' };
  const hint = failures[operation.error_code] || operation.recovery || operation.error_code;
  return (phases[operation.phase] || operation.phase || operation.status) + (hint ? ' ' + hint : '') + (operation.propagation_complete_at * 1000 > Date.now() ? ' Takes effect by ' + time(operation.propagation_complete_at) + '.' : '');
}
function renderAccounts() {
  const openOptions = new Set([...$('accounts').querySelectorAll('details[open]')].map(node => node.closest('.account').dataset.email));
  $('accounts').replaceChildren();
  message('connectedCount', snapshot.accounts.filter(account => account.connection_status === 'connected').length + ' of ' + snapshot.accounts.length + ' connected');
  snapshot.accounts.forEach((account, index) => {
    const draft = draftFor(account), installed = ['connected', 'needs_attention'].includes(account.connection_status), op = account.latest_operation;
    const row = element('article', '', $('accounts')); row.className = 'account'; row.dataset.email = account.email;
    element('h3', account.email, row); element('p', 'Status: ' + account.connection_status.replaceAll('_', ' ') + (account.installed_project_id ? ' · ' + account.installed_project_id : ''), row);
    (account.services || []).forEach(service => element('p', (snapshot.serviceOptions.find(item => item.name === service.name)?.label || service.name) + ': ' + (service.authorization_status === 'granted' ? 'granted' : service.authorization_status) + ' · last check ' + (service.last_verification_status === 'succeeded' ? 'passed' : (service.last_verification_status || 'not checked').replaceAll('_', ' ')) + ' · ' + time(service.last_verified_at), row));
    if (op) element('p', 'Latest attempt: ' + operationText(op), row).className = 'small';
    const status = element('p', draft.dirty ? 'Unsaved changes.' : '', row); status.id = 'account-status-' + index; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const fields = element('fieldset', '', row); element('legend', 'Services', fields);
    snapshot.serviceOptions.forEach(service => {
      const label = element('label', '', fields); label.className = 'check'; const input = element('input', '', label); input.type = 'checkbox'; input.checked = draft.services.includes(service.name);
      input.disabled = installed && (account.services || []).some(item => item.name === service.name);
      label.append(document.createTextNode(service.label + ' — ' + service.description)); input.addEventListener('change', () => { draft.services = [...fields.querySelectorAll('input:checked')].map(item => item.dataset.service); draft.dirty = true; renderAccounts(); $('accounts').children[index].querySelector('[data-service="' + service.name + '"]').focus(); }); input.dataset.service = service.name;
    });
    if (!installed) {
      const label = element('label', '', row); label.className = 'check'; const input = element('input', '', label); input.type = 'checkbox'; input.className = 'owner-access'; input.checked = draft.ownerAccessEnabled;
      label.append(document.createTextNode('Allow my tools to use this account')); input.addEventListener('change', () => { draft.ownerAccessEnabled = input.checked; draft.dirty = true; renderAccounts(); $('accounts').children[index].querySelector('.owner-access').focus(); });
    } else element('p', 'My tools: ' + (active(op) && op.kind === 'access' ? account.owner_access_enabled ? 'enabling…' : 'removing…' : account.owner_access_enabled ? 'enabled' : 'off'), row);
    const selected = snapshot.projects.find(item => item.projectId === draft.projectId);
    const needsSetup = !selected?.clientConfigured || draft.services.some(name => !selected?.apiConfirmed?.[name]);
    const prepare = needsSetup && (!installed || op?.phase === 'authorizing') && !account.recovery_available && !canContinue(op);
    const primary = element('button', prepare ? 'Prepare Google setup' : op?.phase === 'authorizing' ? canContinue(op) ? 'Continue Google consent' : 'Restart Google consent' : installed ? 'Check saved connection' : account.recovery_available ? 'Retry saved authorization' : draft.ownerAccessEnabled ? 'Connect and enable my tools' : 'Connect Google only', row); primary.type = 'button'; primary.disabled = pending.has(account.email) || (active(op) && op.phase !== 'authorizing');
    primary.addEventListener('click', () => prepare ? showProject(draft.projectId, draft.services) : act(account, op?.phase === 'authorizing' ? 'enrollments' : installed ? 'verifications' : account.recovery_available ? 'recoveries' : 'enrollments', status));
    const secondary = element('details', '', row); secondary.open = openOptions.has(account.email); element('summary', 'Account options', secondary);
    const projectLabel = element('label', 'Google project', secondary); const select = element('select', '', projectLabel); choices(select, draft.projectId); select.addEventListener('change', () => { draft.projectId = select.value; draft.dirty = true; renderAccounts(); });
    button(secondary, 'Google setup', () => showProject(draft.projectId, draft.services));
    if (op?.phase === 'authorizing') button(secondary, 'Restart Google consent', () => act(account, 'enrollments', status, true));
    if (installed) button(secondary, 'Reconnect / add selected service', () => act(account, 'enrollments', status, true));
    if (account.recovery_available) button(secondary, 'Retry saved authorization', () => act(account, 'recoveries', status));
    if (installed) button(secondary, account.owner_access_enabled ? 'Remove my tools’ access' : 'Enable my tools’ access', () => act(account, 'access', status));
    if (installed || account.connection_status === 'disconnecting') button(secondary, 'Disconnect', () => { if (confirm('Disconnect ' + account.email + '? Your Google data is not affected.')) act(account, 'connection', status); });
    const google = element('a', 'Revoke at Google ↗', secondary); google.href = 'https://myaccount.google.com/connections'; google.target = '_blank'; google.rel = 'noopener noreferrer';
  });
}
function button(parent, text, action) { const node = element('button', text, parent); node.type = 'button'; node.addEventListener('click', action); return node; }
function showProject(id, services) { reviewedServices = services; $('projectId').value = id; renderProject(); openNextStep(); $('projectGuide').scrollIntoView(); $('projectId').focus(); }
async function act(account, action, status, restart = false) {
  if (pending.has(account.email)) return;
  pending.add(account.email); status.textContent = 'Saving…';
  try {
    const draft = draftFor(account), route = accountRoute(account.email), operation = account.latest_operation;
    const continuing = action === 'enrollments' && !restart && canContinue(operation);
    if (action === 'enrollments' && !continuing) {
      if (!draft.services.length) throw new Error('Select at least one service.');
      if (account.latest_operation?.phase !== 'authorizing') await request(route, 'PUT', { projectId: draft.projectId, services: draft.services });
    }
    const method = action === 'connection' ? 'DELETE' : action === 'access' ? 'PUT' : 'POST';
    const body = action === 'connection' ? undefined : action === 'access' ? { enabled: !account.owner_access_enabled } : action === 'enrollments' ? continuing ? operation.continuation_request : { projectId: draft.projectId, services: draft.services, ownerAccessEnabled: ['connected', 'needs_attention'].includes(account.connection_status) ? account.owner_access_enabled : draft.ownerAccessEnabled, ...(account.latest_operation?.phase === 'authorizing' ? { restart: true } : {}) } : {};
    const key = account.email + ':' + action + ':' + JSON.stringify(body);
    if (!keys.has(key)) keys.set(key, crypto.randomUUID());
    const result = await request(route + '/' + action, method, body, method === 'POST' ? { 'Idempotency-Key': continuing ? operation.continuation_key : keys.get(key) } : {}); keys.delete(key); if (action === 'enrollments' && !continuing) draft.dirty = false;
    if (result.authorizationUrl) { const url = new URL(result.authorizationUrl); if (url.origin !== 'https://accounts.google.com') throw new Error('Unexpected consent address. Refresh and retry.'); location.assign(url.href); }
    else { drafts.delete(account.email); await loadSetup(); }
  } catch (error) { status.textContent = error.message; status.classList.add('error'); }
  finally { pending.delete(account.email); }
}
async function loadSetup() {
  if (loading) return; loading = true; clearTimeout(pollTimer);
  try {
    const first = !snapshot; snapshot = await request('setup'); csrfToken = snapshot.csrfToken;
    const selected = $('projectId').value || snapshot.defaultProjectId;
    $('projectId').replaceChildren(); choices($('projectId'), selected); $('callbackUrl').value = snapshot.callbackUrl;
    if (!projectDirty) renderProject();
    if (first) openNextStep();
    snapshot.accounts.forEach(account => { if (!drafts.get(account.email)?.dirty && !pending.has(account.email)) drafts.delete(account.email); }); renderAccounts();
    $('guide').hidden = false; $('loginLink').hidden = true; message('pageStatus', '');
    $('callerIdentity').textContent = snapshot.ownerCallerArn ? '“My tools” means ' + snapshot.ownerCallerArn : '';
    $('cliConfig').textContent = JSON.stringify({ proxy_base_url: snapshot.proxyUrl, proxy_api_key: '<your API key>', aws_profile: 'krae-jdev-dev-prd' }, null, 2);
    if (snapshot.accounts.some(account => active(account.latest_operation))) pollTimer = setTimeout(loadSetup, 5000);
  } catch (error) { message('pageStatus', error.message, true); }
  finally { loading = false; }
}
$('refresh').addEventListener('click', loadSetup);
$('projectId').addEventListener('change', () => { reviewedServices = undefined; renderProject(); });
$('publishingConfirmed').addEventListener('change', markProjectDirty);
$('saveDefault').addEventListener('click', async () => { try { await request('setup', 'PUT', { defaultProjectId: $('projectId').value }); message('defaultStatus', 'Saved.'); await loadSetup(); } catch (error) { message('defaultStatus', error.message, true); } });
$('saveProject').addEventListener('click', async () => {
  try { const apiConfirmed = {}; $('apiConfirmations').querySelectorAll('input').forEach(input => { apiConfirmed[input.dataset.service] = input.checked; });
    await request('projects/' + encodeURIComponent(project().projectId) + '/setup', 'PUT', { clientRevision: reviewedProjectRevision, publishingConfirmed: $('publishingConfirmed').checked, apiConfirmed });
    projectDirty = false; await loadSetup(); message('projectSaveStatus', 'Saved.');
  } catch (error) { message('projectSaveStatus', error.message, true); }
});
$('addAccountsForm').addEventListener('submit', async event => {
  event.preventDefault(); const emails = [...new Set($('newEmails').value.split(/[,\n]+/).map(email => email.trim().toLowerCase()).filter(Boolean))];
  if (!emails.length || emails.some(email => email.length > 254 || !/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(email))) { message('addStatus', 'Enter valid emails separated by commas or new lines.', true); return; }
  try { for (const email of emails) if (!snapshot.accounts.some(account => account.email === email)) await request(accountRoute(email), 'PUT', { projectId: snapshot.defaultProjectId, services: snapshot.serviceOptions.map(service => service.name) });
    $('newEmails').value = ''; message('addStatus', 'Added.'); await loadSetup();
  } catch (error) { message('addStatus', error.message, true); }
});
async function copy(value, status, fallback) { try { await navigator.clipboard.writeText(value); message(status, 'Copied.'); } catch (_) { if (fallback) { fallback.focus(); fallback.select(); } message(status, 'Copy failed. Select the text and copy it.'); } }
$('copyCallback').addEventListener('click', () => copy($('callbackUrl').value, 'copyStatus', $('callbackUrl')));
for (const [buttonId, inputId, statusId] of [['copyAppName', 'appName', 'appNameStatus'], ['copyWebClientName', 'webClientName', 'webClientNameStatus']]) {
  $(buttonId).addEventListener('click', () => copy($(inputId).value, statusId, $(inputId)));
}
$('copyAdmin').addEventListener('click', () => copy($('adminInstructions').textContent, 'adminStatus'));
$('credentialsForm').addEventListener('submit', async event => {
  event.preventDefault(); const file = $('credentialsFile').files[0]; if (!file) return; $('importCredentials').disabled = true;
  try {
    if (file.size > 65536) throw new Error('Choose a JSON file smaller than 64 KiB.');
    let data; try { data = JSON.parse(await file.text()); } catch (_) { throw new Error('Not a valid client JSON file.'); }
    if (!data?.web || data.installed) throw new Error('Choose a Web application client, not a Desktop client.');
    if (data.web.project_id !== project().projectId) throw new Error('This client belongs to another project. Select that project above.');
    if (!data.web.redirect_uris?.includes($('callbackUrl').value)) throw new Error('The client is missing the redirect URI above. Add it, save, and download the JSON again.');
    await request('projects/' + encodeURIComponent(project().projectId) + '/oauth-client', 'PUT', data); projectDirty = false; await loadSetup(); message('uploadStatus', 'Uploaded. Finish step 4.'); $('confirmStep').open = true;
  } catch (error) { message('uploadStatus', error.message, true); }
  finally { $('importCredentials').disabled = false; $('credentialsFile').value = ''; }
});
const recovery = new URLSearchParams(location.search).get('recovery');
if (recovery) { $('recoveryStatus').hidden = false; $('recoveryStatus').textContent = recovery === 'owner-session-expired' ? 'Your session expired. Sign in, then restart consent for the waiting account.' : 'Google consent did not complete. See the account below.'; }
window.addEventListener('focus', loadSetup);
loadSetup();
