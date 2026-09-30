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
    throw new Error('Your owner session ended. Sign in to resume saved progress and start fresh consent if needed.');
  }
  if (!response.ok) throw new Error(data.detail || data.error?.message || data.message || 'The request failed. Refresh saved progress and try again.');
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
  $('projectContext').textContent = saved.recordedClientType === 'desktop' ? 'Recorded Desktop setup in ' + saved.projectId + '. This does not prove hosted configuration or Google settings.' : 'Console links open ' + saved.projectId + '.';
  message('credentialStatus', saved.clientConfigured ? 'Hosted Web client imported. Publishing and API settings below are your saved confirmations.' : 'A hosted Web client is needed before connecting accounts.');
  $('publishingConfirmed').checked = saved.publishingConfirmed === true;
  $('apiLinks').replaceChildren(); $('apiConfirmations').replaceChildren(); $('serviceDescriptions').replaceChildren();
  snapshot.serviceOptions.forEach(service => {
    const link = element('a', 'Open ' + service.label + ' API ↗', $('apiLinks')); link.dataset.console = 'https://console.cloud.google.com/apis/library/' + service.name + '.googleapis.com'; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const label = element('label', '', $('apiConfirmations')); label.className = 'check';
    const input = element('input', '', label); input.type = 'checkbox'; input.dataset.service = service.name; input.checked = saved.apiConfirmed?.[service.name] === true;
    label.append(document.createTextNode('I checked that ' + service.label + ' API is enabled.')); input.addEventListener('change', markProjectDirty);
    element('li', service.label + ': ' + service.description + ' Requested permission: ' + service.scope, $('serviceDescriptions'));
  });
  document.querySelectorAll('[data-console]').forEach(link => { const url = new URL(link.dataset.console); url.searchParams.set('project', saved.projectId); link.href = url.href; });
  for (const id of ['apisStep', 'audienceStep', 'clientStep', 'confirmStep']) $(id).open = false;
  $(saved.clientConfigured ? saved.publishingConfirmed ? 'apisStep' : 'confirmStep' : 'apisStep').open = true;
  $('saveProject').disabled = !saved.clientConfigured;
  $('adminInstructions').textContent = 'Please review access to Google application ' + (saved.clientId || '(import the Web client to identify it)') + ' in project ' + saved.projectId + '. Requested scopes: ' + snapshot.serviceOptions.filter(service => !reviewedServices || reviewedServices.includes(service.name)).map(service => service.scope).join(', ') + ', openid, email. The Google account owner must complete consent; review your organization’s application access policy.';
  projectDirty = false;
}
function markProjectDirty() { projectDirty = true; message('projectSaveStatus', 'Unsaved project confirmations. Refresh preserves this draft.'); }
function draftFor(account) {
  if (!drafts.has(account.email)) drafts.set(account.email, { projectId: account.selected_project_id || snapshot.defaultProjectId,
    services: [...new Set([...(account.requested_services || ['gmail', 'drive']), ...(account.services || []).map(service => service.name)])], ownerAccessEnabled: account.owner_access_enabled !== false, dirty: false });
  return drafts.get(account.email);
}
function operationText(operation) {
  if (!operation) return 'No recent attempt.';
  const phases = { preparing_consent: 'Preparing Google consent…', preparing_recovery: 'Resuming saved authorization…', checking_candidate: 'Checking saved authorization for the selected services…', applying_access: 'Applying access change. Allow at least 65 seconds for cached permissions to expire.', removing: 'Removing credentials from this proxy…', propagating: 'Access changes saved. Waiting at least 65 seconds for cached permissions to expire.', disconnected: 'Proxy connection removed. Google content and application consent are retained.', failed: 'This attempt did not complete.', canceled: 'This attempt was superseded by another account action.', authorizing: 'Waiting at Google for this account. If the page was closed or Google stopped responding, restart consent. Consent expires after 30 minutes.', validating: 'Checking account identity and selected permissions…', exchanging: 'Receiving Google authorization…', pending_promotion: 'Preparing the saved connection…', verifying: 'Checking each selected service. Allow about 1–2 minutes; you can begin another account.', applying: 'Applying access removal. Allow at least 65 seconds for cached permissions to expire.', disconnecting: 'Removing this proxy connection. Cache propagation takes at least 65 seconds.', ready: 'Checks completed.' };
  const failures = { identity_mismatch: 'Choose this exact Google account when restarting consent.', missing_service_permission: 'Approve every selected service when restarting consent.', refresh_required: 'Durable authorization is missing. Start fresh consent.', broad_allowlist_requires_operator: 'An operator must narrow the caller allowlist before this account can be removed.', consent_expired: 'This consent attempt expired. Restart Google consent.', project_setup_required: 'Review the selected Google project and save its current client settings.', managed_policy_conflict: 'An operator must reconcile this account’s service policy before enrollment can proceed.', consent_denied: 'Google consent was canceled. Restart when you are ready.', provider_unavailable: 'The service check could not complete. Retry the saved connection.' };
  return (phases[operation.phase] || operation.phase || operation.status) + (operation.error_code ? ' ' + (failures[operation.error_code] || operation.error_code) : '') + (operation.recovery ? ' ' + operation.recovery : '') + (operation.authorization_written_at ? ' Authorization written: ' + time(operation.authorization_written_at) + '.' : '') + (operation.propagation_complete_at ? ' Propagation completes: ' + time(operation.propagation_complete_at) + '.' : '');
}
function renderAccounts() {
  $('accounts').replaceChildren();
  message('connectedCount', snapshot.accounts.filter(account => account.connection_status === 'connected').length + ' connected · initial goal: 8 accounts (more are welcome)');
  snapshot.accounts.forEach((account, index) => {
    const draft = draftFor(account), installed = ['connected', 'needs_attention'].includes(account.connection_status), op = account.latest_operation;
    const row = element('article', '', $('accounts')); row.className = 'account'; row.dataset.email = account.email;
    element('h3', account.email, row); element('p', 'Saved connection: ' + account.connection_status.replaceAll('_', ' ') + (account.installed_project_id ? ' · ' + account.installed_project_id : ''), row);
    (account.services || []).forEach(service => element('p', (snapshot.serviceOptions.find(item => item.name === service.name)?.label || service.name) + ': ' + (service.authorization_status === 'granted' ? 'Google permission granted' : service.authorization_status) + ' · last check ' + (service.last_verification_status === 'succeeded' ? 'passed' : (service.last_verification_status || 'not checked').replaceAll('_', ' ')) + ' · ' + time(service.last_verified_at), row));
    element('p', 'Latest attempt: ' + operationText(op), row).className = 'small';
    const status = element('p', draft.dirty ? 'Unsaved account choices.' : '', row); status.id = 'account-status-' + index; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const fields = element('fieldset', '', row); element('legend', installed ? 'Services for a repair or added permission' : 'Select services before consent', fields);
    snapshot.serviceOptions.forEach(service => {
      const label = element('label', '', fields); label.className = 'check'; const input = element('input', '', label); input.type = 'checkbox'; input.checked = draft.services.includes(service.name);
      input.disabled = installed && (account.services || []).some(item => item.name === service.name);
      label.append(document.createTextNode(service.label + ' — ' + service.description)); input.addEventListener('change', () => { draft.services = [...fields.querySelectorAll('input:checked')].map(item => item.dataset.service); draft.dirty = true; renderAccounts(); $('accounts').children[index].querySelector('[data-service="' + service.name + '"]').focus(); }); input.dataset.service = service.name;
    });
    if (!installed) {
      const label = element('label', '', row); label.className = 'check'; const input = element('input', '', label); input.type = 'checkbox'; input.className = 'owner-access'; input.checked = draft.ownerAccessEnabled;
      label.append(document.createTextNode('Allow my tools to use this account')); input.addEventListener('change', () => { draft.ownerAccessEnabled = input.checked; draft.dirty = true; renderAccounts(); $('accounts').children[index].querySelector('.owner-access').focus(); });
      element('p', 'Connecting enables the selected services for your configured tools only when this is checked. Google consent alone does not grant caller access.', row).className = 'small';
    } else element('p', 'My tools: ' + (active(op) && op.kind === 'access' ? account.owner_access_enabled ? 'enabling access…' : 'removing access…' : account.owner_access_enabled ? 'access enabled' : 'access off; Google connection retained'), row);
    const selected = snapshot.projects.find(item => item.projectId === draft.projectId);
    const needsSetup = !selected?.clientConfigured || !selected?.publishingConfirmed || draft.services.some(name => !selected?.apiConfirmed?.[name]);
    const prepare = needsSetup && (!installed || op?.phase === 'authorizing') && !account.recovery_available && !canContinue(op);
    const primary = element('button', prepare ? 'Prepare Google setup' : op?.phase === 'authorizing' ? canContinue(op) ? 'Continue Google consent' : 'Restart Google consent' : installed ? 'Check saved connection' : account.recovery_available ? 'Retry saved authorization' : draft.ownerAccessEnabled ? 'Connect and enable my tools' : 'Connect Google only', row); primary.type = 'button'; primary.disabled = pending.has(account.email) || (active(op) && op.phase !== 'authorizing');
    primary.addEventListener('click', () => prepare ? showProject(draft.projectId, draft.services) : act(account, op?.phase === 'authorizing' ? 'enrollments' : installed ? 'verifications' : account.recovery_available ? 'recoveries' : 'enrollments', status));
    const secondary = element('details', '', row); element('summary', 'Account options', secondary);
    const projectLabel = element('label', 'Google project', secondary); const select = element('select', '', projectLabel); choices(select, draft.projectId); select.addEventListener('change', () => { draft.projectId = select.value; draft.dirty = true; renderAccounts(); });
    button(secondary, 'Google setup / administrator instructions', () => showProject(draft.projectId, draft.services));
    if (op?.phase === 'authorizing') button(secondary, 'Restart Google consent', () => act(account, 'enrollments', status, true));
    if (installed) button(secondary, 'Reconnect / add selected service', () => act(account, 'enrollments', status, true));
    if (account.recovery_available) button(secondary, 'Retry saved authorization', () => act(account, 'recoveries', status));
    if (installed) button(secondary, account.owner_access_enabled ? 'Remove my tools’ access' : 'Enable my tools’ access', () => act(account, 'access', status));
    if (installed || account.connection_status === 'disconnecting') button(secondary, 'Disconnect this proxy account', () => { if (confirm('Remove this account’s credentials and access from this proxy? Google content and Google’s application consent are kept.')) act(account, 'connection', status); });
    const google = element('a', 'Manage Google application consent separately ↗', secondary); google.href = 'https://myaccount.google.com/connections'; google.target = '_blank'; google.rel = 'noopener noreferrer';
    element('p', 'Removing consent in Google can affect this account’s other connections using the same application.', secondary).className = 'small';
  });
}
function button(parent, text, action) { const node = element('button', text, parent); node.type = 'button'; node.addEventListener('click', action); return node; }
function showProject(id, services) { reviewedServices = services; $('projectId').value = id; renderProject(); $('projectGuide').scrollIntoView(); $('projectId').focus(); }
async function act(account, action, status, restart = false) {
  if (pending.has(account.email)) return;
  pending.add(account.email); status.textContent = 'Saving this account’s action…';
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
    snapshot = await request('setup'); csrfToken = snapshot.csrfToken;
    const selected = $('projectId').value || snapshot.defaultProjectId;
    $('projectId').replaceChildren(); choices($('projectId'), selected); $('callbackUrl').value = snapshot.callbackUrl;
    if (!projectDirty) renderProject();
    snapshot.accounts.forEach(account => { if (!drafts.get(account.email)?.dirty && !pending.has(account.email)) drafts.delete(account.email); }); renderAccounts();
    $('guide').hidden = false; $('loginLink').hidden = true; message('pageStatus', 'Saved progress loaded. Unsaved drafts stay local.');
    $('callerIdentity').textContent = 'Configured caller for “my tools”: ' + (snapshot.ownerCallerArn || 'not supplied by the server') + '.';
    $('cliConfig').textContent = JSON.stringify({ proxy_base_url: snapshot.proxyUrl, proxy_api_key: '<owner-provided API key>', aws_profile: 'krae-jdev-dev-prd' }, null, 2);
    if (snapshot.accounts.some(account => active(account.latest_operation))) pollTimer = setTimeout(loadSetup, 5000);
  } catch (error) { message('pageStatus', error.message, true); }
  finally { loading = false; }
}
$('refresh').addEventListener('click', loadSetup);
$('projectId').addEventListener('change', () => { reviewedServices = undefined; renderProject(); });
$('publishingConfirmed').addEventListener('change', markProjectDirty);
$('saveDefault').addEventListener('click', async () => { try { await request('setup', 'PUT', { defaultProjectId: $('projectId').value }); message('defaultStatus', 'Default project saved.'); await loadSetup(); } catch (error) { message('defaultStatus', error.message, true); } });
$('saveProject').addEventListener('click', async () => {
  try { const apiConfirmed = {}; $('apiConfirmations').querySelectorAll('input').forEach(input => { apiConfirmed[input.dataset.service] = input.checked; });
    await request('projects/' + encodeURIComponent(project().projectId) + '/setup', 'PUT', { clientRevision: reviewedProjectRevision, publishingConfirmed: $('publishingConfirmed').checked, apiConfirmed });
    projectDirty = false; await loadSetup(); message('projectSaveStatus', 'Reviewed settings saved for this client revision.');
  } catch (error) { message('projectSaveStatus', error.message, true); }
});
$('addAccountsForm').addEventListener('submit', async event => {
  event.preventDefault(); const emails = [...new Set($('newEmails').value.split(/[,\n]+/).map(email => email.trim().toLowerCase()).filter(Boolean))];
  if (!emails.length || emails.some(email => email.length > 254 || !/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(email))) { message('addStatus', 'Enter valid emails separated by commas or new lines. This draft has not been saved.', true); return; }
  try { for (const email of emails) if (!snapshot.accounts.some(account => account.email === email)) await request(accountRoute(email), 'PUT', { projectId: snapshot.defaultProjectId, services: snapshot.serviceOptions.map(service => service.name) });
    $('newEmails').value = ''; message('addStatus', 'Accounts saved.'); await loadSetup();
  } catch (error) { message('addStatus', error.message, true); }
});
async function copy(value, status, fallback) { try { await navigator.clipboard.writeText(value); message(status, 'Copied.'); } catch (_) { if (fallback) { fallback.focus(); fallback.select(); } message(status, 'Select and copy the text above.'); } }
$('copyCallback').addEventListener('click', () => copy($('callbackUrl').value, 'copyStatus', $('callbackUrl')));
$('copyAdmin').addEventListener('click', () => copy($('adminInstructions').textContent, 'adminStatus'));
$('credentialsForm').addEventListener('submit', async event => {
  event.preventDefault(); const file = $('credentialsFile').files[0]; if (!file) return; $('importCredentials').disabled = true;
  try {
    if (file.size > 65536) throw new Error('Choose a JSON file smaller than 64 KiB.');
    let data; try { data = JSON.parse(await file.text()); } catch (_) { throw new Error('Choose valid downloaded client JSON.'); }
    if (!data?.web || data.installed) throw new Error('Choose a Web application client, not a Desktop client.');
    if (data.web.project_id !== project().projectId) throw new Error('The file belongs to another project. Select that project or download the correct Web client.');
    if (!data.web.redirect_uris?.includes($('callbackUrl').value)) throw new Error('Add this exact callback, save the Web client, then download its JSON again.');
    await request('projects/' + encodeURIComponent(project().projectId) + '/oauth-client', 'PUT', data); projectDirty = false; await loadSetup(); message('uploadStatus', 'Web client saved securely. Review and save its publishing confirmations.'); $('confirmStep').open = true;
  } catch (error) { message('uploadStatus', error.message, true); }
  finally { $('importCredentials').disabled = false; $('credentialsFile').value = ''; }
});
const recovery = new URLSearchParams(location.search).get('recovery');
if (recovery) { $('recoveryStatus').hidden = false; $('recoveryStatus').textContent = recovery === 'owner-session-expired' ? 'Sign in again, then restart consent for the waiting account. Saved connections are preserved.' : 'Google consent did not complete. Review the account’s latest attempt and restart consent if needed.'; }
window.addEventListener('focus', loadSetup);
loadSetup();
