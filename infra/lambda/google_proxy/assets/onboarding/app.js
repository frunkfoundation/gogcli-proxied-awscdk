'use strict';
const $ = id => document.getElementById(id);
const apiBase = new URL('v1/', new URL('./', location.href));
let snapshot, csrfToken = '', loads = 0, pollTimer, callbackFocused = false;
const links = new Map(), notes = new Map(), drafts = new Map(), pending = new Set(), keys = new Map(), order = [];
const WAITING = ['awaiting_connection', 'preparing_consent', 'authorizing'];
const BUSY = { enrollments: 'Opening Google…', verifications: 'Starting check…', recoveries: 'Retrying…', access: 'Saving…', connection: 'Disconnecting…' };
function message(id, text, error = false) { $(id).textContent = text; $(id).classList.toggle('error', error); }
function element(tag, text, parent) { const node = document.createElement(tag); if (text) node.textContent = text; if (parent) parent.append(node); return node; }
function button(parent, text, action, className = 'secondary') { const node = element('button', text, parent); node.type = 'button'; node.className = className; node.addEventListener('click', action); return node; }
async function request(route, method = 'GET', body, headers = {}) {
  const response = await fetch(new URL(route, apiBase), { method, credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(method !== 'GET' ? { 'X-CSRF-Token': csrfToken } : {}), ...headers },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
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
const linkWaiting = op => op?.auth_mode === 'connection' && active(op) && WAITING.includes(op.phase);
const installed = account => ['connected', 'needs_attention'].includes(account.connection_status);
const verified = account => account.connection_status === 'connected' && account.services?.length && account.services.every(service => service.authorization_status === 'granted' && service.last_verification_status === 'succeeded');
const ready = services => snapshot.application.clientConfigured && services.every(name => snapshot.application.apiConfirmed?.[name]);
const time = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString() : 'not checked';
const serviceLabels = names => names.map(name => snapshot.serviceOptions.find(service => service.name === name)?.label || name).join(' and ');
function draftFor(account) {
  if (!drafts.has(account.email)) drafts.set(account.email, { services: accountServices(account), dirty: false });
  return drafts.get(account.email);
}
function operationText(op, email, services) {
  const phases = { awaiting_connection: 'Waiting for the connection link to be opened.', preparing_consent: 'Preparing Google consent…', preparing_recovery: 'Resuming saved authorization…', checking_candidate: 'Checking connection…', applying_access: 'Applying tool access change…', removing: 'Disconnecting…', propagating: 'Applying access change…', disconnected: 'Disconnected.', failed: 'Last attempt failed.', canceled: 'Consent canceled.', authorizing: 'Waiting for Google approval.', validating: 'Checking connection…', exchanging: 'Receiving Google authorization…', pending_promotion: 'Saving connection…', verifying: 'Checking connection…', applying: 'Removing access…', disconnecting: 'Disconnecting…', ready: 'Connection check complete.' };
  const failures = { identity_mismatch: 'Choose ' + email + ' at Google, then approve the selected services.', missing_service_permission: 'Approve every selected service when restarting Google consent.', refresh_required: 'Reconnect this account.', broad_allowlist_requires_operator: 'Contact the operator to narrow the caller allowlist, then retry.', connection_link_expired: 'The connection link expired. Create a new link.', consent_expired: 'Consent expired. Restart Google consent.', application_setup_required: 'gogcli-proxy is not ready to connect this account. Contact the operator, then retry.', managed_policy_conflict: 'Contact the operator to reconcile this account’s service policy, then retry.', consent_denied: 'Google consent was canceled. Restart Google consent.', workspace_restricted: 'This account’s Workspace administrator must approve gogcli-proxy. Contact the administrator, then restart consent.', api_disabled: 'gogcli-proxy is not ready for this service. Contact the operator, then retry.', provider_unavailable: 'The connection check failed. Retry the saved authorization if available.' };
  const waiting = op.propagation_complete_at * 1000 > Date.now();
  if (active(op) && op.phase === 'verifying' && op.promoted_at && !op.error_code) return 'Google approved access. ' + (waiting ? 'Waiting for the access update before checking ' + serviceLabels(services) + '. The connection check can start after ' + time(op.propagation_complete_at) + '.' : 'Checking ' + serviceLabels(services) + ' through gogcli-proxy…');
  return (phases[op.phase] || op.phase || op.status) + (op.error_code ? ' ' + (failures[op.error_code] || op.recovery || 'Retry, or contact the operator if the problem continues.') : '') + (waiting ? ' Waiting for the access update until ' + time(op.propagation_complete_at) + '.' : '');
}
function badgeFor(account, op) {
  if (linkWaiting(op)) return op.phase === 'awaiting_connection' ? ['Awaiting link', 'info'] : ['Waiting for Google', 'info'];
  if (active(op)) return op.phase === 'authorizing' ? ['Waiting for Google', 'info'] : ['Working…', 'info'];
  if (account.recovery_available) return ['Needs retry', 'bad'];
  if (installed(account) && (!account.uses_current_app || account.connection_status === 'needs_attention')) return ['Reconnect needed', 'warn'];
  if (verified(account)) return ['Connected', 'ok'];
  if (installed(account)) return ['Not checked', 'warn'];
  return ['Not connected', op && ['failed', 'canceled'].includes(op.status) ? 'bad' : 'muted'];
}
// The link action comes first: most accounts live in another browser profile.
function actionsFor(account, draft) {
  const op = account.latest_operation, hasInstalled = installed(account), additions = draft.services.filter(name => !installedServices(account).includes(name));
  const link = text => ({ text, action: 'link' }), here = { text: hasInstalled ? 'Reconnect in this browser' : 'Connect in this browser', action: 'enrollments' };
  if (linkWaiting(op)) return [];
  if (active(op) && op.phase !== 'authorizing') return [{ text: ['checking_candidate', 'validating', 'verifying'].includes(op.phase) ? 'Checking…' : 'Working…', disabled: true }];
  if (op?.phase === 'authorizing') return canContinue(op) ? [{ text: 'Continue Google consent', action: 'enrollments' }, { text: 'Restart Google consent', action: 'enrollments', restart: true }] : [{ text: 'Restart Google consent', action: 'enrollments' }];
  if (account.recovery_available) return [{ text: 'Retry connection', action: 'recoveries' }];
  if (op?.auth_mode === 'connection' && op.status !== 'succeeded') return [link('Create new link'), here];
  if (!hasInstalled || !account.uses_current_app || account.connection_status === 'needs_attention' || additions.length) return [link('Create connection link'), here];
  return [{ text: 'Check connection', action: 'verifications' }];
}
function renderAccount(account, row, index) {
  const draft = draftFor(account), op = account.latest_operation, hasInstalled = installed(account), checked = verified(account), busy = pending.has(account.email);
  const additions = draft.services.filter(name => !installedServices(account).includes(name)), actions = actionsFor(account, draft);
  const open = row.querySelector('details')?.open; row.replaceChildren();
  const line = (text, className = 'small') => { if (text) element('p', text, row).className = className; };
  const head = element('div', '', row); head.className = 'account-head'; element('h3', account.email, head);
  const [badge, tone] = badgeFor(account, op); element('span', badge, head).className = 'badge ' + tone;
  if (active(op) && !linkWaiting(op)) line(operationText(op, account.email, installedServices(account)), '');
  else if (account.recovery_available) line('The connection check needs a retry.', '');
  else if (hasInstalled && !account.uses_current_app) line(account.connection_status === 'connected' ? 'Your existing connection still works. It stays available until the replacement succeeds.' : 'Your existing connection is retained and needs repair. Reconnect to replace its authorization.', '');
  if (hasInstalled) {
    line((checked ? 'Connected services: ' : 'Authorized services: ') + serviceLabels(installedServices(account)), '');
    const latest = Math.max(0, ...account.services.map(service => service.last_verified_at || 0));
    line(latest ? 'Last check: ' + account.services.map(service => serviceLabels([service.name]) + ' ' + (service.authorization_status !== 'granted' ? 'not authorized' : service.last_verification_status === 'succeeded' ? 'passed' : service.last_verification_status.replaceAll('_', ' '))).join(', ') + ' · ' + time(latest) : 'Connection not checked yet.');
  }
  if (account.owner_access_enabled === false || hasInstalled) line('Tool access: ' + (active(op) && ['access', 'disconnect'].includes(op.kind) ? 'applying change…' : account.owner_access_enabled === false ? 'paused' : checked ? 'enabled' : 'awaiting connection check'));
  if (op?.phase === 'authorizing' && op.auth_mode !== 'connection' && !canContinue(op)) line(op.consent_expires_at * 1000 <= Date.now() ? 'Google consent expired. Restart to get a new approval link.' : 'Google consent cannot continue in this owner session. Restart Google consent.');
  if (op && !active(op) && !(op.status === 'succeeded' && op.phase === 'ready')) line((hasInstalled && op.status === 'failed' && op.kind === 'enrollment' ? 'Last reconnect failed. ' : '') + operationText(op, account.email, installedServices(account)));
  const fields = element('fieldset', '', row); fields.className = 'services'; element('legend', 'Services', fields);
  const chips = element('div', '', fields);
  snapshot.serviceOptions.forEach(service => {
    const label = element('label', '', chips); label.className = 'chip'; label.title = service.description;
    const input = element('input', '', label); input.type = 'checkbox'; input.checked = draft.services.includes(service.name); input.dataset.service = service.name; input.dataset.focus = service.name;
    input.disabled = installedServices(account).includes(service.name) || busy || active(op);
    label.append(document.createTextNode(service.label));
    input.addEventListener('change', () => { draft.services = [...chips.querySelectorAll('input:checked')].map(item => item.dataset.service); draft.dirty = true; renderAccounts(); });
  });
  if (hasInstalled && additions.length && actions.some(item => item.action === 'enrollments')) line(serviceLabels(additions) + ' will be added when you reconnect.');
  // Continuing an existing consent reuses its frozen client; every new attempt needs a ready application.
  const needsApp = item => ['link', 'enrollments'].includes(item.action) && !(item.action === 'enrollments' && !item.restart && canContinue(op));
  const notReady = actions.some(needsApp) && !ready(draft.services);
  if (actions.some(needsApp) && account.owner_access_enabled === false) line('Tool access will remain paused after connecting.');
  const bar = element('div', '', row); bar.className = 'actions';
  actions.forEach((item, number) => {
    const control = button(bar, item.text, () => item.action === 'link' ? mintLink(account, control) : act(account, item.action, control, item.restart), number ? 'secondary' : 'primary');
    control.dataset.focus = item.action || 'busy'; if (item.action) control.dataset.action = item.action;
    control.disabled = item.disabled || busy || needsApp(item) && notReady;
  });
  const note = notes.get(account.email), status = element('p', note?.text || (notReady ? 'gogcli-proxy is not ready to connect this account' + (snapshot.application.clientConfigured ? ' for ' + serviceLabels(draft.services.filter(name => !snapshot.application.apiConfirmed?.[name])) : '') + '. Contact the operator, then retry.' : ''), row);
  status.className = 'note' + (note?.error ? ' error' : ''); status.id = 'account-status-' + index; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  if (linkWaiting(op)) renderLink(account, op, row, busy, status);
  if (hasInstalled) renderManage(account, op, draft, row, actions, busy).open = !!open;
}
function renderLink(account, op, row, busy, status) {
  const panel = element('div', '', row), link = links.get(account.email), url = link?.operationId === op.id && link.connectionUrl;
  panel.className = 'connection-link';
  const cancel = () => { const control = button(panel, 'Cancel link', () => cancelLink(account, control, op.connection_link_id), 'link-button'); control.dataset.focus = 'cancel-link'; control.disabled = busy; };
  const replace = text => { const control = button(panel, text, () => mintLink(account, control), 'primary'); control.dataset.focus = 'link'; control.dataset.action = 'link'; control.disabled = busy || !ready(draftFor(account).services); };
  if (op.phase !== 'awaiting_connection') { element('p', 'Google approval is in progress in the browser profile that opened the link.', panel); return cancel(); }
  if (op.connection_expires_at * 1000 <= Date.now()) { element('p', 'This connection link expired.', panel); return replace('Create new link'); }
  if (!url) {
    element('p', 'Waiting for the connection link to be opened in the browser profile for ' + account.email + '. Expires ' + time(op.connection_expires_at) + '.', panel);
    element('p', 'Links are shown only when created. If you no longer have it, create a new one; the old link stops working.', panel).className = 'small';
    replace('Create new link'); return cancel();
  }
  element('p', 'Open this link in the browser profile signed in to ' + account.email + '. No auth.k-rae.com sign-in is needed there. Expires ' + time(op.connection_expires_at) + '.', panel);
  const copyRow = element('div', '', panel); copyRow.className = 'copy-row';
  const field = element('input', '', copyRow); field.className = 'connection-url'; field.readOnly = true; field.value = url; field.setAttribute('aria-label', 'Connection link for ' + account.email); field.dataset.focus = 'connection-url';
  field.addEventListener('focus', () => field.select());
  const copy = button(copyRow, 'Copy link', async () => {
    try { await navigator.clipboard.writeText(url); copy.textContent = 'Copied ✓'; setTimeout(() => { copy.textContent = 'Copy link'; }, 2000); }
    catch { field.focus(); status.textContent = 'The browser blocked copying. The link is selected; press ⌘C or Ctrl+C.'; }
  }, 'primary');
  copy.dataset.focus = 'link'; // Same key as Create: focus lands on Copy once the link appears.
  copy.dataset.action = 'copy';
  cancel();
}
function renderManage(account, op, draft, row, actions, busy) {
  const manage = element('details', '', row), shown = new Set(actions.map(item => item.action));
  element('summary', 'Manage', manage).dataset.focus = 'manage';
  const box = element('div', '', manage); box.className = 'actions';
  const add = (text, onClick, disabled, focus) => { const control = button(box, text, () => onClick(control)); control.dataset.focus = focus; control.disabled = busy || disabled; return control; };
  const working = active(op) && op.phase !== 'authorizing';
  if (!shown.has('verifications')) add('Check connection', control => act(account, 'verifications', control), active(op), 'manage-check');
  if (!shown.has('enrollments')) add('Reconnect', control => act(account, 'enrollments', control, true), active(op) || !ready(draft.services), 'manage-reconnect');
  if (!shown.has('link') && !linkWaiting(op)) add('Create connection link', control => mintLink(account, control), working || !ready(draft.services), 'manage-link').dataset.action = 'link';
  const paused = account.owner_access_enabled === false;
  add(paused ? 'Resume tool access' : 'Pause tool access', control => act(account, 'access', control), active(op) && paused, 'manage-access');
  add('Disconnect', control => { if (confirm('Disconnect ' + account.email + '? Your Google data is not affected.')) act(account, 'connection', control); }, op?.kind === 'disconnect' && active(op), 'manage-disconnect');
  const google = element('a', 'Revoke at Google ↗', manage); google.href = 'https://myaccount.google.com/connections'; google.target = '_blank'; google.rel = 'noopener noreferrer';
  return manage;
}
function renderAccounts() {
  const focused = document.activeElement, email = focused.closest?.('.account')?.dataset.email, focusKey = focused.dataset?.focus;
  const complete = snapshot.accounts.filter(account => account.uses_current_app && verified(account)).length;
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
async function run(account, control, busyText, task) {
  if (pending.has(account.email)) return;
  pending.add(account.email); notes.delete(account.email);
  // aria-busy instead of disabled keeps focus on the control; the pending guard ignores repeat clicks.
  if (control) { control.setAttribute('aria-busy', 'true'); control.textContent = busyText; }
  try { if (await task() === 'leaving') return; } catch (error) { notes.set(account.email, { text: error.message, error: true }); }
  pending.delete(account.email); renderAccounts(); await loadSetup();
}
function mintLink(account, control) {
  return run(account, control, 'Creating link…', async () => {
    const op = account.latest_operation, body = { services: draftFor(account).services, replace: !!active(op) && WAITING.includes(op.phase) }, key = account.email + ':connection-links:' + JSON.stringify(body);
    if (!body.services.length) throw new Error('Select at least one service.');
    if (!keys.has(key)) keys.set(key, crypto.randomUUID());
    const link = await request(accountRoute(account.email) + '/connection-links', 'POST', body, { 'Idempotency-Key': keys.get(key) });
    keys.delete(key);
    if (!link.connectionUrl) throw new Error('This link has expired or was canceled. Create a new link.');
    if (new URL(link.connectionUrl).origin !== location.origin) throw new Error('Unexpected connection link address.');
    links.set(account.email, link); drafts.delete(account.email);
  });
}
function cancelLink(account, control, linkId) {
  return run(account, control, 'Canceling…', async () => { await request('connection-links/' + encodeURIComponent(linkId), 'DELETE'); links.delete(account.email); });
}
function act(account, action, control, restart = false) {
  return run(account, control, BUSY[action], async () => {
    const draft = draftFor(account), op = account.latest_operation, continuing = action === 'enrollments' && !restart && canContinue(op);
    if (action === 'enrollments' && !continuing && !draft.services.length) throw new Error('Select at least one service.');
    const method = action === 'connection' ? 'DELETE' : action === 'access' ? 'PUT' : 'POST';
    const body = action === 'connection' ? undefined : action === 'access' ? { enabled: account.owner_access_enabled === false } : action === 'enrollments' ? continuing ? op.continuation_request : { services: draft.services, ownerAccessEnabled: account.owner_access_enabled !== false, ...(op?.phase === 'authorizing' ? { restart: true } : {}) } : {};
    const key = account.email + ':' + action + ':' + JSON.stringify(body); if (!keys.has(key)) keys.set(key, crypto.randomUUID());
    const result = await request(accountRoute(account.email) + '/' + action, method, body, method === 'POST' ? { 'Idempotency-Key': continuing ? op.continuation_key : keys.get(key) } : {}); keys.delete(key);
    if (result.authorizationUrl) { const url = new URL(result.authorizationUrl); if (url.origin !== 'https://accounts.google.com') throw new Error('Unexpected consent address. Refresh and retry.'); location.assign(url.href); return 'leaving'; }
    drafts.delete(account.email);
  });
}
// Only the newest load renders; background polls skip unchanged data so controls are not rebuilt mid-click.
async function loadSetup(force = false) {
  const seq = ++loads; clearTimeout(pollTimer);
  try {
    const next = await request('setup');
    if (seq !== loads) return;
    const changed = force || JSON.stringify(next) !== JSON.stringify(snapshot);
    snapshot = next; csrfToken = next.csrfToken;
    for (const [email, link] of links) if (next.accounts.find(account => account.email === email)?.latest_operation?.id !== link.operationId) links.delete(email);
    next.accounts.forEach(account => { if (!drafts.get(account.email)?.dirty && !pending.has(account.email)) drafts.delete(account.email); });
    if (changed) renderAccounts();
    $('guide').hidden = false; $('loginLink').hidden = true; message('pageStatus', ''); message('updated', 'Updated ' + new Date().toLocaleTimeString());
    const operation = new URLSearchParams(location.search).get('operation');
    if (operation && !callbackFocused) { const account = next.accounts.find(item => item.latest_operation?.id === operation); if (account) { [...$('accounts').children].find(row => row.dataset.email === account.email)?.focus(); callbackFocused = true; } }
    if (next.accounts.some(account => active(account.latest_operation))) pollTimer = setTimeout(loadSetup, 5000);
  } catch (error) { if (seq === loads) message('pageStatus', error.message, true); }
}
$('refresh').addEventListener('click', async () => {
  const control = $('refresh'); control.disabled = true; control.textContent = 'Refreshing…';
  await loadSetup(true); control.disabled = false; control.textContent = 'Refresh';
});
$('addAccountsForm').addEventListener('submit', async event => {
  event.preventDefault(); const emails = [...new Set($('newEmails').value.split(/[,\n]+/).map(email => email.trim().toLowerCase()).filter(Boolean))];
  if (!emails.length || emails.some(email => email.length > 254 || !/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(email))) { message('addStatus', 'Enter valid emails separated by commas or new lines.', true); return; }
  try { for (const email of emails) if (!snapshot.accounts.some(account => account.email === email)) await request(accountRoute(email), 'PUT', { services: ['gmail', 'drive', 'calendar'] });
    $('newEmails').value = ''; message('addStatus', 'Added.'); await loadSetup(true);
  } catch (error) { message('addStatus', error.message, true); }
});
const recovery = new URLSearchParams(location.search).get('recovery');
if (recovery) { $('recoveryStatus').hidden = false; $('recoveryStatus').textContent = recovery === 'owner-session-expired' ? 'Your session expired. Sign in, then restart consent for the waiting account.' : 'Google consent did not complete. See the account below.'; }
window.addEventListener('focus', () => loadSetup());
// Returning with Back from Google can restore this page from cache with a click still marked as running.
window.addEventListener('pageshow', event => { if (event.persisted) { pending.clear(); loadSetup(true); } });
loadSetup();
