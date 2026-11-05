"""Single-application HTTP commands preserve accepted work and installed provenance."""
import copy
import json
import onboarding
import onboarding_runtime as runtime
import os
import unittest
from unittest.mock import patch

from onboarding_services import content_hash
from onboarding_store import WorkflowError
import test_onboarding_lifecycle as lifecycle


class ApplicationContractTests(unittest.TestCase):
    setUp = lifecycle.AccountLifecycleTests.setUp
    enroll = lifecycle.AccountLifecycleTests.enroll

    def test_snapshot_reads_only_canonical_client_and_persisted_accounts(self):
        self.store.put({'pk': 'SETUP', 'default_project_id': 'fixture-other'})
        self.store.put({'pk': 'CLIENT#fixture-other', 'project_id': 'fixture-other', 'secret_ref': 'private'})
        snapshot = self.service.setup('owner')
        self.assertEqual(['jay@example.com'], [a['email'] for a in snapshot['accounts']])
        self.assertEqual('gogcli-proxy', snapshot['application']['projectId'])
        self.assertEqual({'application', 'serviceOptions', 'accounts'}, set(snapshot))
        self.assertEqual([], self.service.setup('other-owner')['accounts'])
        self.assertFalse(snapshot['accounts'][0]['uses_current_app'])
        self.assertNotIn('selected_project_id', snapshot['accounts'][0])
        self.assertNotIn('installed_project_id', snapshot['accounts'][0])

    def test_installed_provenance_is_never_relabelled(self):
        account = self.store.get('ACCOUNT#jay@example.com')
        account.update(credential_revision='installed', connection_status='connected',
                       installed_project_id='fixture-previous', selected_project_id='gogcli-proxy')
        self.store.put(account)
        self.assertFalse(self.service.setup('owner')['accounts'][0]['uses_current_app'])
        account['installed_project_id'] = 'gogcli-proxy'
        self.store.put(account)
        self.assertTrue(self.service.setup('owner')['accounts'][0]['uses_current_app'])

    def test_new_project_choices_and_unknown_account_fields_are_rejected(self):
        for body in ({'projectId': 'gogcli-proxy'}, {'unexpected': True}):
            with self.subTest(body=body), self.assertRaises(WorkflowError) as failed:
                self.service.save_account('owner', 'new@example.com', body)
            self.assertEqual('invalid_account', failed.exception.code)
        with self.assertRaises(WorkflowError) as failed:
            self.enroll(projectId='gogcli-proxy')
        self.assertEqual('invalid_enrollment', failed.exception.code)
        self.assertFalse(self.store.rows('OP#'))

    def test_missing_or_other_application_binding_fails_closed(self):
        for value in (None, 'fixture-other'):
            with patch.dict(os.environ, clear=False):
                if value is None:
                    os.environ.pop('GOOGLE_PROJECT_ID', None)
                else:
                    os.environ['GOOGLE_PROJECT_ID'] = value
                with self.assertRaises(WorkflowError) as failed:
                    self.enroll()
                self.assertEqual('application_not_configured', failed.exception.code)
        self.assertFalse(self.store.rows('OP#'))

    def test_saved_pause_without_credentials_survives_connect_override(self):
        account = self.store.get('ACCOUNT#jay@example.com')
        account['desired_owner_access'] = False
        self.store.put(account)
        self.enroll(ownerAccessEnabled=True)
        self.assertFalse(self.store.get(account['pk'])['desired_owner_access'])

    def test_default_connect_enables_tools_and_ignores_obsolete_selection(self):
        account = self.store.get('ACCOUNT#jay@example.com')
        account['selected_project_id'] = 'fixture-previous'
        self.store.put(account)
        operation = self.enroll()
        self.assertTrue(self.store.get(account['pk'])['desired_owner_access'])
        self.assertEqual('gogcli-proxy', self.store.get('OP#' + operation['id'])['project_id'])

    def test_old_exact_replay_and_callback_keep_original_client_after_new_import(self):
        for project in ('fixture-previous', 'gogcli-proxy'):
            with self.subTest(project=project):
                if self.store.get('ACCOUNT#jay@example.com').get('active_operation'):
                    current = self.store.get('ACCOUNT#jay@example.com')
                    current.pop('active_operation')
                    self.store.put(current)
                accepted = self.enroll(key=project)
                operation = self.store.get('OP#' + accepted['id'])
                original = {'projectId': project, 'services': ['gmail', 'drive'], 'ownerAccessEnabled': True}
                client = {'client_id': project + '-client', 'client_secret': 'synthetic', 'project_id': project}
                ref = self.store.secret_put('clients/' + project, client)
                operation.update(command_request=original, project_id=project, client_ref=ref, client_revision='pinned')
                self.store.put(operation)
                idem = self.store.get(operation['idempotency_pk'])
                idem['fingerprint'] = content_hash(original)
                self.store.put(idem)
                with patch.dict(os.environ, {'ONBOARDING_BASE_URL': 'https://app.example/onboarding/'}), patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service', return_value=self.service):
                    auth.return_value.session.return_value = {'owner': 'owner', 'session_id': 'session'}
                    result = onboarding.handler({'path': '/onboarding/v1/accounts/jay@example.com/enrollments',
                        'httpMethod': 'POST', 'headers': {'Idempotency-Key': project}, 'body': json.dumps(copy.deepcopy(original))}, None)
                    self.assertEqual(202, result['statusCode'])
                    replay = json.loads(result['body'])
                self.assertEqual(accepted['id'], replay['id'])
                self.assertEqual(original, self.service.setup('owner', 'session')['accounts'][0]['latest_operation']['continuation_request'])
                with self.assertRaises(WorkflowError) as failed:
                    self.service.enroll('owner', 'jay@example.com', project, 'session', {**original, 'restart': True})
                self.assertEqual('idempotency_conflict', failed.exception.code)
                self.service.import_client({'web': {'client_id': 'replacement', 'client_secret': 'new',
                    'project_id': 'gogcli-proxy', 'redirect_uris': [self.service.callback_url]}})
                state = self.store.secret_get(operation['attempt_ref'])['state']
                with patch.dict(os.environ, {'ONBOARDING_BASE_URL': 'https://app.example/onboarding/'}), patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service', return_value=self.service):
                    auth.return_value.session.return_value = {'owner': 'owner', 'session_id': 'session'}
                    result = onboarding.handler({'path': '/onboarding/oauth/google/callback', 'httpMethod': 'GET',
                        'queryStringParameters': {'state': state, 'code': 'synthetic-code'}}, None)
                    self.assertEqual(303, result['statusCode'])
                    self.assertTrue(result['headers']['Location'].endswith('?operation=' + accepted['id']))
                self.assertEqual(client, self.google.exchange.call_args.args[0])
                current = self.store.get(operation['pk'])
                self.assertEqual(ref, current['client_ref'])
                self.assertEqual('pinned', current['client_revision'])
                self.assertEqual(project, current['project_id'])
                # Restore review for the next accepted fixture command.
                self.service.review_application({'clientRevision': self.store.get('CLIENT#gogcli-proxy')['revision'],
                    'apiConfirmed': {'gmail': True, 'drive': True}})
