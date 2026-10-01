"""Frozen capabilities and scoped consent exercise the existing account lifecycle."""
import os
import unittest
from unittest.mock import Mock, patch

from onboarding_links import ConnectionLinks
from onboarding_operations import OnboardingService
from onboarding_store import WorkflowError, digest
from test_onboarding_lifecycle import MemoryStore


class LinkStore(MemoryStore):
    def accept(self, account, operation, idem, changes, supersede=False):
        super().accept(account, operation, idem, changes, supersede)
        if operation.get('connection_link_id'):
            self.put({'pk': 'LINK#' + operation['connection_link_id'], 'operation_id': operation['id'],
                      'capability_hash': operation['capability_hash']}, absent=True)
            for prep_key in ('LINKPREP#' + idem['pk'].removeprefix('IDEM#'), 'LINKCLEANUP#' + operation['id']):
                row = self.get(prep_key)
                self.put({**row, 'expires_at': operation['created_at'] + 604800})

    def _connection_transaction(self, op, changes, now, binding=None, cancel=False):
        current = self.get(op['pk'])
        account = self.get('ACCOUNT#' + op['email'])
        if account['generation'] != op['generation'] or account.get('active_operation') != op['id'] or current['phase'] != op['phase']:
            raise WorkflowError(409, 'operation_conflict', 'changed')
        if binding is not None and (current.get('session_hash') or current['link_expires_at'] <= now):
            raise WorkflowError(409, 'operation_conflict', 'claimed')
        if binding is None and not cancel and current.get('session_hash') != op['session_hash']:
            raise WorkflowError(409, 'operation_conflict', 'binding changed')
        updated = {**current, **changes, 'updated_at': now}
        self.put(updated)
        if cancel:
            account.update(generation=op['generation'] + 1)
            account.pop('active_operation', None)
            self.put(account)
        return updated

    from onboarding_links_store import ConnectionStore
    bind_connection = ConnectionStore.bind_connection
    start_connection = ConnectionStore.start_connection
    cancel_connection = ConnectionStore.cancel_connection


class ConnectionLinkTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, GOOGLE_PROJECT_ID='gogcli-proxy', GOOGLE_CALLBACK_URL='https://app.example/onboarding/oauth/google/callback')
        self.env.start(); self.addCleanup(self.env.stop)
        self.store, self.google = LinkStore(), Mock()
        self.service = OnboardingService(self.store, self.google, 'https://app.example/onboarding/oauth/google/callback')
        self.links = ConnectionLinks(self.service, 'https://app.example/onboarding')
        self.service.save_account('owner', 'jay@example.com', {'services': ['gmail']})
        self.store.put({'pk': 'CLIENT#gogcli-proxy', 'secret_ref': '/client', 'revision': 'r1', 'api_confirmed': {'gmail': True}})
        self.store.secrets['/client'] = {'client_id': 'client', 'client_secret': 'secret'}
        self.google.authorization_url.side_effect = lambda *args: 'https://accounts.google.com?state=' + args[2]
        self.google.exchange.return_value = {'id_token': 'id', 'refresh_token': 'refresh'}

    def mint(self, key='mint', body=None):
        result = self.links.mint('owner', 'jay@example.com', key, body or {'services': ['gmail']})
        capability = result['connectionUrl'].split('.')[-1]
        return result, capability

    def bound(self):
        result, capability = self.mint()
        self.links.claim(result['id'], capability, 'target')
        return result

    def test_mint_replay_waits_without_google_and_freezes_fields(self):
        result, _ = self.mint()
        self.assertEqual(result, self.links.mint('owner', 'jay@example.com', 'mint', {'services': ['gmail']}))
        self.google.authorization_url.assert_not_called()
        op = self.store.get('OP#' + result['operationId'])
        self.assertEqual('awaiting_connection', op['phase'])
        self.assertEqual('r1', op['client_revision'])
        self.assertNotIn('connectionUrl', self.service.operation('owner', op['id']))
        with self.assertRaises(WorkflowError):
            self.links.mint('owner', 'jay@example.com', 'mint', {'services': ['drive']})

    def test_claim_replay_and_wrong_binding_do_not_rebind(self):
        result, capability = self.mint()
        first = self.links.claim(result['id'], capability, 'target')
        self.assertEqual(first, self.links.claim(result['id'], capability, 'target'))
        with self.assertRaises(WorkflowError):
            self.links.claim(result['id'], capability, 'other')
        self.assertEqual(digest('target'), self.store.get('OP#' + result['operationId'])['session_hash'])

    def test_continue_and_callback_replay_exchange_once(self):
        result = self.bound()
        first = self.links.consent(result['operationId'], 'target', 'continue')
        self.assertEqual(first, self.links.consent(result['operationId'], 'target', 'continue'))
        op = self.store.get('OP#' + result['operationId'])
        state = self.store.secret_get(op['attempt_ref'])['state']
        with self.assertRaises(WorkflowError):
            self.service.callback(state, 'code', 'other')
        self.service.callback(state, 'code', 'target')
        self.service.callback(state, 'code', 'target')
        self.assertEqual(1, self.google.exchange.call_count)

    def test_replacement_and_cancel_revoke_target_access(self):
        result = self.bound()
        self.mint('replace', {'services': ['gmail'], 'replace': True})
        with self.assertRaises(WorkflowError):
            self.links.operation(result['operationId'], 'target')
        latest = self.store.get('ACCOUNT#jay@example.com')['active_operation']
        op = self.store.get('OP#' + latest)
        self.links.cancel('owner', op['connection_link_id'])
        self.links.cancel('owner', op['connection_link_id'])
        self.assertEqual('canceled', self.store.get(op['pk'])['phase'])

    def test_wrong_identity_failure_preserves_installed_credentials_and_access(self):
        from onboarding_store import account_parameter
        from onboarding_worker import process
        account = self.store.get('ACCOUNT#jay@example.com')
        account.update(credential_revision='old', installed_services=['gmail'], google_subject='old-subject',
                       desired_owner_access=False, connection_status='connected')
        self.store.put(account)
        credential_ref = account_parameter(self.store.prefix, account['email'])
        self.store.parameter_put(credential_ref, {'refresh_token': 'old-refresh'})
        result = self.bound()
        self.links.consent(result['operationId'], 'target', 'continue')
        op = self.store.get('OP#' + result['operationId'])
        state = self.store.secret_get(op['attempt_ref'])['state']
        self.service.callback(state, 'code', 'target')
        self.google.identity.side_effect = WorkflowError(400, 'identity_mismatch', 'Wrong account.')
        with patch('onboarding_google.GoogleClient', return_value=self.google):
            process(op['id'], self.store, Mock())
        latest = self.store.get(op['pk'])
        self.assertEqual('failed', latest['phase'])
        self.assertEqual('identity_mismatch', latest['error_code'])
        self.assertEqual({'refresh_token': 'old-refresh'}, self.store.secret_get(credential_ref))
        account = self.store.get('ACCOUNT#jay@example.com')
        self.assertEqual('old', account['credential_revision'])
        self.assertFalse(account['desired_owner_access'])
        self.assertNotIn('candidate_ref', latest)

    def test_waiting_expiry_and_started_consent_get_separate_deadlines(self):
        from onboarding_worker import process
        with patch('time.time', return_value=100):
            result, capability = self.mint()
        with patch('time.time', return_value=86499):
            self.links.claim(result['id'], capability, 'target')
            self.links.consent(result['operationId'], 'target', 'continue')
        op = self.store.get('OP#' + result['operationId'])
        self.assertEqual(88299, op['consent_expires_at'])
        with patch('time.time', return_value=86501):
            self.assertTrue(self.links.operation(op['id'], 'target')['canContinue'])
            self.assertIn('authorizationUrl', self.links.consent(op['id'], 'target', 'reopen'))
        self.links.cancel('owner', result['id'])
        with patch('time.time', return_value=100000):
            waiting, _ = self.mint('second')
        with patch('time.time', return_value=186400):
            process(waiting['operationId'], self.store, Mock(), now=186400)
        self.assertEqual('connection_link_expired', self.store.get('OP#' + waiting['operationId'])['error_code'])

    def test_prepared_command_retry_accepts_reserved_operation_once(self):
        with patch.object(self.store, 'accept', side_effect=WorkflowError(409, 'operation_conflict', 'interrupted')):
            with self.assertRaises(WorkflowError):
                self.mint()
        prep = self.store.rows('LINKPREP#')[0]
        result, _ = self.mint()
        self.assertEqual(prep['operation_id'], result['operationId'])
        self.assertEqual(1, len(self.store.rows('OP#')))
        self.assertEqual(1, self.store.get('ACCOUNT#jay@example.com')['generation'])

    def test_cancel_and_restrictive_owner_actions_fence_late_callbacks(self):
        result = self.bound()
        self.links.consent(result['operationId'], 'target', 'continue')
        op = self.store.get('OP#' + result['operationId'])
        state = self.store.secret_get(op['attempt_ref'])['state']
        self.service.disconnect('owner', 'jay@example.com')
        with self.assertRaises(WorkflowError):
            self.service.callback(state, 'code', 'target')
        self.google.exchange.assert_not_called()
        with self.assertRaises(WorkflowError):
            self.links.operation(op['id'], 'target')

    def test_target_cookie_expires_and_terminal_summary_does_not_extend_it(self):
        result = self.bound()
        op = self.store.get('OP#' + result['operationId'])
        self.store.terminal(op, {'phase': 'failed', 'status': 'failed', 'error_code': 'consent_denied'}, op['created_at'])
        with patch('time.time', return_value=op['connection_session_expires_at'] - 1):
            self.assertEqual('failed', self.links.operation(op['id'], 'target')['phase'])
        with patch('time.time', return_value=op['connection_session_expires_at']):
            with self.assertRaises(WorkflowError) as failed:
                self.links.operation(op['id'], 'target')
            self.assertEqual(401, failed.exception.status)

    def test_started_consent_preparation_interruption_never_mints_second_attempt(self):
        result = self.bound()
        with patch.object(self.google, 'authorization_url', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.links.consent(result['operationId'], 'target', 'continue')
        self.assertEqual('preparing_consent', self.links.consent(result['operationId'], 'target', 'continue')['phase'])
        self.assertEqual(0, len(self.store.rows('STATE#')))
        self.assertEqual(1, len(self.store.rows('OP#')))

    def test_delayed_prepared_acceptance_extends_both_artifact_cleanup_deadlines(self):
        with patch('time.time', return_value=100), patch.object(self.store, 'accept', side_effect=WorkflowError(409, 'operation_conflict', 'interrupted')):
            with self.assertRaises(WorkflowError):
                self.mint()
        with patch('time.time', return_value=604800):
            result, _ = self.mint()
        for prefix in ('LINKPREP#', 'LINKCLEANUP#'):
            self.assertEqual(1209600, self.store.rows(prefix)[0]['expires_at'])
        with patch('time.time', return_value=604901):
            replay = self.links.mint('owner', 'jay@example.com', 'mint', {'services': ['gmail']})
        self.assertEqual(result['connectionUrl'], replay['connectionUrl'])

    def test_expired_unaccepted_preparation_requires_a_new_command_key(self):
        with patch('time.time', return_value=100), patch.object(self.store, 'accept', side_effect=WorkflowError(409, 'operation_conflict', 'interrupted')):
            with self.assertRaises(WorkflowError):
                self.mint()
        with patch('time.time', return_value=604900), self.assertRaises(WorkflowError) as failed:
            self.mint()
        self.assertEqual('idempotency_expired', failed.exception.code)
        self.assertEqual([], self.store.rows('OP#'))
