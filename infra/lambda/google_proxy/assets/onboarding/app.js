'use strict';
const $ = id => document.getElementById(id);
const apiBase = new URL('v1/', new URL('./', location.href));
let snapshot, csrfToken = '', loading = false, pollTimer, callbackFocused = false;
const drafts = new Map(), pending = new Set(), keys = new Map(), order = [];
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
    throw new Error('Your session ended. Sign in again to return to your account progress.');
  }
  if (!response.ok) throw new Error(data.detail || data.error?.message || data.message || 'Request failed. Try again.');
  return data;
}
const accountRoute = email => 'accounts/' + encodeURIComponent(email);
const installedServices = account => (account.services || []).map(service => service.name);
const accountServices = account => [...new Set([...(account.requested_services || ['gmail', 'drive', 'calendar']), ...installedServices(account)])];
const canContinue = op => op?.phase === 'authorizing' && op.consent_session_matches === true && op.continuation_key && op.continuation_request && op.consent_expires_at * 1000 > Date.now();
const active = op => op && ['accepted', 'running', 'applying'].includes(op.status);
const installed = account => ['connected', 'needs_attention'].includes(account.connection_status);
const time = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString() : 'not checked';
const serviceLabels = names => names.map(name => snapshot.serviceOptions.find(service => service.name === name)?.label || name).join(' and ');
function draftFor(account) {
  if (!drafts.has(account.email)) drafts.set(account.email, { services: accountServices(account), dirty: false });
  return drafts.get(account.email);
}
function operationText(op, email) {
  const phases = { preparing_consent: 'Preparing Google consent…', preparing_recovery: 'Resuming saved authorization…', checking_candidate: 'Checking connection…', applying_access: 'Applying tool access change…', removing: 'Disconnecting…', propagating: 'Applying access change…', disconnected: 'Disconnected.', failed: 'Last attempt failed.', canceled: 'Consent canceled.', authorizing: 'Waiting for Google approval.', validating: 'Checking connection…', exchanging: 'Receiving Google authorization…', pending_promotion: 'Saving connection…', verifying: 'Checking connection…', applying: 'Removing access…', disconnecting: 'Disconnecting…', ready: 'Connection check complete.' };
  const failures = { identity_mismatch: 'Choose ' + email + ' at Google, then approve the selected services.', missing_service_permission: 'Approve every selected service when restarting Google consent.', refresh_required: 'Reconnect this account.', broad_allowlist_requires_operator: 'Contact the operator to narrow the caller allowlist, then retry.', consent_expired: 'Consent expired. Restart Google consent.', application_setup_required: 'gogcli-proxy is not ready to connect this account. Contact the operator, then retry.', managed_policy_conflict: 'Contact the operator to reconcile this account’s service policy, then retry.', consent_denied: 'Google consent was canceled. Restart Google consent.', workspace_restricted: 'This account’s Workspace administrator must approve gogcli-proxy. Contact the administrator, then restart consent.', api_disabled: 'gogcli-proxy is not ready for this service. Contact the operator, then retry.', provider_unavailable: 'The connection check failed. Retry the saved authorization if available.' };
  return (phases[op.phase] || op.phase || op.status) + (op.error_code ? ' ' + (failures[op.error_code] || op.recovery || 'Retry, or contact the operator if the problem continues.') : '') + (op.propagation_complete_at * 1000 > Date.now() ? ' Takes effect by ' + time(op.propagation_complete_at) + '.' : '');
}
function primaryFor(account, draft) {
  const op = account.latest_operation, additions = draft.services.filter(name => !installedServices(account).includes(name));
  if (active(op) && op.phase !== 'authorizing') return { text: operationText(op, account.email), disabled: true };
  if (op?.phase === 'authorizing') return { text: canContinue(op) ? 'Continue Google consent' : 'Restart Google consent', action: 'enrollments' };
  if (account.recovery_available) return { text: 'Retry connection', action: 'recoveries' };
  if (!installed(account)) return { text: 'Connect', action: 'enrollments' };
  if (!account.uses_current_app || account.connection_status === 'needs_attention') return { text: 'Reconnect', action: 'enrollments' };
  if (additions.length) return { text: 'Reconnect to add ' + serviceLabels(additions), action: 'enrollments' };
  return { text: 'Check connection', action: 'verifications' };
}
function renderAccount(account, row, index) {
  const draft = draftFor(account), op = account.latest_operation, primary = primaryFor(account, draft), hasInstalled = installed(account);
  const open = row.querySelector('details')?.open; row.replaceChildren();
  element('h3', account.email, row);
  let state = hasInstalled ? account.uses_current_app ? account.connection_status === 'connected' ? 'Connected' : 'Reconnect required' : 'Reconnect required for gogcli-proxy' : 'Not connected';
  if (active(op)) state = op.phase === 'authorizing' ? 'Waiting for Google approval' : primary.text;
  else if (account.recovery_available) state = 'Connection check needs retry';
  element('p', state, row);
  if (hasInstalled && !account.uses_current_app) element('p', account.connection_status === 'connected' ? 'Your existing connection still works. It stays available until the replacement succeeds.' : 'Your existing connection is retained and needs repair. Reconnect to replace its authorization.', row);
  element('p', 'Tool access: ' + (active(op) && ['access', 'disconnect'].includes(op.kind) ? 'applying change…' : account.owner_access_enabled === false ? 'paused' : account.owner_access_enabled && hasInstalled ? 'enabled' : 'available after connection'), row);
  element('p', 'Connected services: ' + (installedServices(account).length ? serviceLabels(installedServices(account)) : 'none'), row);
  (account.services || []).forEach(service => element('p', serviceLabels([service.name]) + ': ' + (service.authorization_status === 'granted' ? 'authorized' : service.authorization_status) + ' · last check ' + (service.last_verification_status === 'succeeded' ? 'passed' : (service.last_verification_status || 'not checked').replaceAll('_', ' ')) + ' · ' + time(service.last_verified_at), row).className = 'small');
  if (op?.phase === 'authorizing' && !canContinue(op)) element('p', op.consent_expires_at * 1000 <= Date.now() ? 'Google consent expired. Restart to get a new approval link.' : 'Google consent cannot continue in this owner session. Restart Google consent.', row);
  if (op) element('p', (hasInstalled && op.status === 'failed' && op.kind === 'enrollment' ? 'Last reconnect failed. ' : '') + operationText(op, account.email), row).className = 'small';
  const status = element('p', draft.dirty ? 'Service choices will be saved when you connect.' : '', row); status.id = 'account-status-' + index; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.tabIndex = -1;
  const fields = element('fieldset', '', row); element('legend', 'Services', fields);
  snapshot.serviceOptions.forEach(service => {
    const label = element('label', '', fields); label.className = 'check'; const input = element('input', '', label); input.type = 'checkbox'; input.checked = draft.services.includes(service.name); input.dataset.service = service.name; input.dataset.focus = service.name;
    input.disabled = installedServices(account).includes(service.name) || pending.has(account.email) || active(op);
    label.append(document.createTextNode(service.label + ' — ' + service.description));
    input.addEventListener('change', () => { draft.services = [...fields.querySelectorAll('input:checked')].map(item => item.dataset.service); draft.dirty = true; renderAccounts(); });
  });
  const needsReady = primary.action === 'enrollments' && !canContinue(op) && (!snapshot.application.clientConfigured || draft.services.some(name => !snapshot.application.apiConfirmed?.[name]));
  if (needsReady) status.textContent = 'gogcli-proxy is not ready to connect this account' + (draft.services.some(name => !snapshot.application.apiConfirmed?.[name]) ? ' for ' + serviceLabels(draft.services.filter(name => !snapshot.application.apiConfirmed?.[name])) : '') + '. Contact the operator, then retry.';
  if (primary.action === 'enrollments') element('p', 'Choose this account at Google and approve the selected services. ' + (account.owner_access_enabled === false ? 'Tool access will remain paused.' : 'Connecting enables access for your authorized tools.'), row);
  const main = button(row, primary.text, () => act(account, primary.action, status)); main.dataset.focus = 'primary'; main.className = primary.action === 'enrollments' ? 'connect-account' : ''; main.disabled = primary.disabled || needsReady || pending.has(account.email);
  const options = element('details', '', row); options.open = !!open; element('summary', 'Connection options', options).dataset.focus = 'options';
  if (hasInstalled) {
    element('p', 'Installed services stay selected. Reconnect can add services; contact the operator to change service restrictions.', options).className = 'small';
    button(options, 'Check connection', () => act(account, 'verifications', status)).disabled = active(op);
    const reconnect = button(options, 'Reconnect', () => act(account, 'enrollments', status, true)); reconnect.disabled = active(op) || !snapshot.application.clientConfigured || draft.services.some(name => !snapshot.application.apiConfirmed?.[name]);
    const access = button(options, account.owner_access_enabled === false ? 'Resume tool access' : 'Pause tool access', () => act(account, 'access', status)); access.dataset.restrictive = account.owner_access_enabled === false ? '' : 'true'; access.disabled = active(op) && account.owner_access_enabled === false;
    const disconnect = button(options, 'Disconnect', () => { if (confirm('Disconnect ' + account.email + '? Your Google data is not affected.')) act(account, 'connection', status); }); disconnect.dataset.restrictive = op?.kind === 'disconnect' && active(op) ? '' : 'true';
    const google = element('a', 'Revoke at Google ↗', options); google.href = 'https://myaccount.google.com/connections'; google.target = '_blank'; google.rel = 'noopener noreferrer';
  }
  if (op?.phase === 'authorizing') button(options, 'Restart Google consent', () => act(account, 'enrollments', status, true)).disabled = !snapshot.application.clientConfigured || draft.services.some(name => !snapshot.application.apiConfirmed?.[name]);
  if (options.children.length === 1) options.remove();
  options.querySelectorAll('button').forEach((control, number) => { control.dataset.focus = 'option-' + number; if (pending.has(account.email) || active(op) && op.phase !== 'authorizing' && control.dataset.restrictive !== 'true') control.disabled = true; });
}
function renderAccounts() {
  const focused = document.activeElement, email = focused.closest?.('.account')?.dataset.email, focusKey = focused.dataset?.focus;
  const complete = snapshot.accounts.filter(account => account.uses_current_app && account.connection_status === 'connected' && account.services?.length && account.services.every(service => service.authorization_status === 'granted' && service.last_verification_status === 'succeeded')).length;
  const previous = snapshot.accounts.filter(account => installed(account) && !account.uses_current_app).length;
  message('connectedCount', complete + ' of ' + snapshot.accounts.length + ' connected to gogcli-proxy' + (previous ? '; ' + previous + ' existing connection' + (previous === 1 ? '' : 's') + ' awaiting reconnection' : ''));
  snapshot.accounts.forEach(account => { if (!order.includes(account.email)) order.push(account.email); });
  for (const savedEmail of order) {
    const account = snapshot.accounts.find(item => item.email === savedEmail); if (!account) continue;
    let row = [...$('accounts').children].find(node => node.dataset.email === savedEmail);
    if (!row) { row = element('article', '', $('accounts')); row.className = 'account'; row.dataset.email = savedEmail; row.tabIndex = -1; }
    renderAccount(account, row, order.indexOf(savedEmail));
  }
  if (email && focusKey) { const row = [...$('accounts').children].find(node => node.dataset.email === email), control = row?.querySelector('[data-focus="' + focusKey + '"]'); (control && !control.disabled ? control : row)?.focus({ preventScroll: true }); }
}
function button(parent, text, action) { const node = element('button', text, parent); node.type = 'button'; node.addEventListener('click', action); return node; }
async function act(account, action, status, restart = false) {
  if (pending.has(account.email) || !action) return;
  pending.add(account.email); status.textContent = 'Saving…';
  try {
    const draft = draftFor(account), op = account.latest_operation, continuing = action === 'enrollments' && !restart && canContinue(op);
    if (action === 'enrollments' && !continuing && !draft.services.length) throw new Error('Select at least one service.');
    const method = action === 'connection' ? 'DELETE' : action === 'access' ? 'PUT' : 'POST';
    const body = action === 'connection' ? undefined : action === 'access' ? { enabled: account.owner_access_enabled === false } : action === 'enrollments' ? continuing ? op.continuation_request : { services: draft.services, ownerAccessEnabled: account.owner_access_enabled !== false, ...(op?.phase === 'authorizing' ? { restart: true } : {}) } : {};
    const key = account.email + ':' + action + ':' + JSON.stringify(body); if (!keys.has(key)) keys.set(key, crypto.randomUUID());
    const result = await request(accountRoute(account.email) + '/' + action, method, body, method === 'POST' ? { 'Idempotency-Key': continuing ? op.continuation_key : keys.get(key) } : {}); keys.delete(key);
    if (result.authorizationUrl) { const url = new URL(result.authorizationUrl); if (url.origin !== 'https://accounts.google.com') throw new Error('Unexpected consent address. Refresh and retry.'); location.assign(url.href); }
    else { drafts.delete(account.email); pending.delete(account.email); await loadSetup(); }
  } catch (error) { status.textContent = error.message; status.classList.add('error'); }
  finally { pending.delete(account.email); }
}
async function loadSetup() {
  if (loading) return; loading = true; clearTimeout(pollTimer);
  try {
    snapshot = await request('setup'); csrfToken = snapshot.csrfToken;
    snapshot.accounts.forEach(account => { if (!drafts.get(account.email)?.dirty && !pending.has(account.email)) drafts.delete(account.email); }); renderAccounts();
    $('guide').hidden = false; $('loginLink').hidden = true; message('pageStatus', '');
    const operation = new URLSearchParams(location.search).get('operation');
    if (operation && !callbackFocused) { const account = snapshot.accounts.find(item => item.latest_operation?.id === operation); if (account) { [...$('accounts').children].find(row => row.dataset.email === account.email)?.focus(); callbackFocused = true; } }
    if (snapshot.accounts.some(account => active(account.latest_operation))) pollTimer = setTimeout(loadSetup, 5000);
  } catch (error) { message('pageStatus', error.message, true); }
  finally { loading = false; }
}
$('refresh').addEventListener('click', loadSetup);
$('addAccountsForm').addEventListener('submit', async event => {
  event.preventDefault(); const emails = [...new Set($('newEmails').value.split(/[,\n]+/).map(email => email.trim().toLowerCase()).filter(Boolean))];
  if (!emails.length || emails.some(email => email.length > 254 || !/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(email))) { message('addStatus', 'Enter valid emails separated by commas or new lines.', true); return; }
  try { for (const email of emails) if (!snapshot.accounts.some(account => account.email === email)) await request(accountRoute(email), 'PUT', { services: ['gmail', 'drive', 'calendar'] });
    $('newEmails').value = ''; message('addStatus', 'Added.'); await loadSetup();
  } catch (error) { message('addStatus', error.message, true); }
});
const recovery = new URLSearchParams(location.search).get('recovery');
if (recovery) { $('recoveryStatus').hidden = false; $('recoveryStatus').textContent = recovery === 'owner-session-expired' ? 'Your session expired. Sign in, then restart consent for the waiting account.' : 'Google consent did not complete. See the account below.'; }
window.addEventListener('focus', loadSetup);
loadSetup();
