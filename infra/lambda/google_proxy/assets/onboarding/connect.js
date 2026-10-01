'use strict';
const $ = id => document.getElementById(id);
const base = new URL(document.currentScript.src.replace(/connect\.js(?:\?.*)?$/, ''));
const api = new URL('connection/v1/', base);
let operationId = new URLSearchParams(location.search).get('operation'), csrfToken = '', timer, busy = false, startKey, revision = 0;
const time = value => new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString();
function status(text, error = false) { $('connectionStatus').textContent = text; $('connectionStatus').classList.toggle('error', error); }
function recovery(text) { $('connectionRecovery').textContent = text; $('connectionRecovery').hidden = !text; }
async function request(route, method = 'GET', body, headers = {}) {
  const response = await fetch(new URL(route, api), { method, credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json', ...headers }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.safeMessage || 'This connection is unavailable. Ask the account owner for a new connection link.');
  return data;
}
function render(op) {
  csrfToken = op.csrfToken || csrfToken;
  $('connectionDetails').hidden = false;
  $('connectionEmail').textContent = op.email;
  $('connectionServices').textContent = 'Selected services: ' + (op.services || []).map(service => ({ gmail: 'Gmail', drive: 'Drive', calendar: 'Calendar' })[service] || service).join(', ');
  const expiry = op.consentExpiresAt || op.expiresAt;
  $('connectionExpiry').textContent = expiry ? (op.consentExpiresAt ? 'Google approval expires ' : 'Connection link expires ') + time(expiry) + '.' : '';
  $('continueGoogle').hidden = !op.canContinue;
  $('continueGoogle').disabled = busy;
  const phases = { awaiting_connection: 'Ready to connect. Check the account and selected services, then continue to Google.', preparing_consent: 'Preparing Google approval…', authorizing: 'Waiting for Google approval. Continue to return to the same approval.', exchanging: 'Receiving Google authorization…', validating: 'Checking your connection…', checking_candidate: 'Checking your connection…', pending_promotion: 'Saving your connection…', applying_access: 'Applying access…', verifying: 'Checking your connection…', ready: 'Your account is connected.' };
  const expired = op.canContinue === false && (op.phase === 'awaiting_connection' || op.phase === 'authorizing' && op.consentExpiresAt * 1000 <= Date.now());
  const terminal = ['failed', 'canceled'].includes(op.status);
  const failures = { identity_mismatch: 'Google approved a different account. This connection requires ' + op.email + '.', missing_service_permission: 'Google did not approve every selected service.', consent_denied: 'Google approval was canceled.', consent_expired: 'Google approval expired.', connection_link_expired: 'This connection link expired.', refresh_required: 'Google did not provide the authorization needed to keep this account connected.' };
  status((expired ? (op.phase === 'awaiting_connection' ? 'This connection link expired.' : 'Google approval expired.') : '') || failures[op.errorCode] || op.safeMessage || (terminal ? 'This connection did not complete.' : phases[op.phase] || (op.status === 'succeeded' ? 'Your account is connected.' : 'Connection in progress…')), terminal || expired);
  recovery(terminal || expired ? 'Ask the account owner for a new connection link. No sign-in to auth.k-rae.com is needed in this browser profile.' : '');
  clearTimeout(timer);
  if (['accepted', 'running', 'applying'].includes(op.status)) timer = setTimeout(load, 5000);
}
async function load() {
  if (!operationId) return;
  const current = revision;
  try { const result = await request('operations/' + encodeURIComponent(operationId)); if (current === revision) render(result); }
  catch (error) { if (current !== revision) return; clearTimeout(timer); $('continueGoogle').hidden = true; status(error.message, true); recovery('Ask the account owner for a new connection link.'); }
}
$('continueGoogle').addEventListener('click', async () => {
  if (busy) return;
  const current = revision;
  busy = true; $('continueGoogle').disabled = true; clearTimeout(timer);
  if (!startKey) startKey = crypto.randomUUID();
  try {
    const result = await request('operations/' + encodeURIComponent(operationId) + '/consents', 'POST', {}, { 'X-CSRF-Token': csrfToken, 'Idempotency-Key': startKey });
    if (current !== revision) return;
    if (result.authorizationUrl) {
      const url = new URL(result.authorizationUrl);
      if (url.origin !== 'https://accounts.google.com') throw new Error('The Google approval address is unavailable. Ask the account owner for a new connection link.');
      location.assign(url.href);
    } else { startKey = undefined; render(result); }
  } catch (error) { if (current !== revision) return; status(error.message, true); recovery('Use Continue to Google to retry this request, or ask the account owner for a new connection link.'); }
  finally { if (current === revision) { busy = false; $('continueGoogle').disabled = false; } }
});
$('refreshConnection').addEventListener('click', load);
window.addEventListener('focus', () => { if (!busy) load(); });
async function open() {
  const fragment = location.hash.slice(1), current = ++revision;
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  if (fragment) {
    clearTimeout(timer); operationId = undefined; csrfToken = ''; startKey = undefined; busy = false;
    $('connectionDetails').hidden = true; $('continueGoogle').hidden = true; recovery(''); status('Opening connection…');
    const separator = fragment.indexOf('.');
    if (separator < 1) { status('This connection link is incomplete.', true); recovery('Ask the account owner for a new connection link.'); return; }
    try {
      const result = await request('claims', 'POST', { linkId: fragment.slice(0, separator), capability: fragment.slice(separator + 1) });
      if (current !== revision) return;
      operationId = result.operationId || result.id;
      const target = new URL('connect/result', base); target.searchParams.set('operation', operationId);
      history.replaceState(null, '', target.pathname + target.search);
      render(result); await load();
    } catch (error) { if (current !== revision) return; status(error.message, true); recovery('Ask the account owner for a new connection link.'); }
  } else if (operationId) await load();
  else { status('Open the connection link supplied by your account owner.', true); recovery('Ask the account owner for a new connection link.'); }
}
window.addEventListener('hashchange', open);
open();
