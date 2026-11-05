"""Offline operator contract checks; no AWS credentials or live Google calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure_google_application', ROOT / 'scripts/configure_google_application.py')
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        env = self.source / 'env/gogcli-prd'
        env.mkdir(parents=True)
        self.binding = json.loads((ROOT / 'env/gogcli-prd/binding.json').read_text())
        self.binding['google_application'] = {'project_id': 'gogcli-proxy', 'project_number': '1042630585914',
            'name': 'gogcli-proxy', 'organization': 'kraecollective.com'}
        self.access = json.loads((ROOT / 'env/gogcli-prd/access.json').read_text())
        self.env = env
        self.write_target()
        self.callback = self.access['GogcliGoogleProxyApi']['GoogleCallbackUrl']
        self.document = {'web': {'project_id': 'gogcli-proxy', 'client_id': 'client.apps.googleusercontent.com',
            'client_secret': 'SECRET-NEVER-PRINT', 'redirect_uris': [self.callback],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token'}}
        self.client_file = self.source / 'download.json'
        self.client_file.write_text(json.dumps(self.document))

    def write_target(self):
        (self.env / 'binding.json').write_text(json.dumps(self.binding))
        (self.env / 'access.json').write_text(json.dumps(self.access))

    def test_real_target_and_client_parser_rejects_wrong_inputs_before_aws(self):
        session_factory = Mock()
        for change in ({'project_id': 'another-project'}, {'redirect_uris': ['https://wrong.example/callback']},
                       {'token_uri': 'https://wrong.example/token'}, {'auth_uri': 'https://wrong.example/auth'}):
            with self.subTest(change=change):
                document = {'web': {**self.document['web'], **change}}
                self.client_file.write_text(json.dumps(document))
                with self.assertRaises(Exception):
                    operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', session_factory)
        self.client_file.write_text(json.dumps({'installed': self.document['web']}))
        with self.assertRaises(Exception):
            operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', session_factory)
        session_factory.assert_not_called()

    def test_target_callback_must_match_binding(self):
        self.access['GogcliGoogleProxyApi']['GoogleCallbackUrl'] = 'https://wrong.example/callback'
        self.write_target()
        with self.assertRaises(ValueError):
            operator.load_target(self.source, 'gogcli-prd')

    def test_explicit_session_and_account_guard_before_provisioning(self):
        factory = Mock()
        factory.return_value.client.return_value.get_caller_identity.return_value = {'Account': 'wrong'}
        with self.assertRaises(ValueError):
            operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', factory)
        factory.assert_called_once_with(profile_name=self.binding['deployment_profile'], region_name=self.binding['region'])
        factory.return_value.resource.assert_not_called()

    def session(self):
        factory = Mock()
        factory.return_value.client.return_value.get_caller_identity.return_value = {'Account': self.binding['account_id']}
        return factory

    def test_real_provisioning_reuses_revision_and_records_only_confirmed_apis(self):
        from test_onboarding_lifecycle import MemoryStore
        store = MemoryStore()
        factory = self.session()
        with patch.object(operator, 'Store', return_value=store):
            first = operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail', 'drive'], 'testing', factory)
            second = operator.configure(self.source, 'gogcli-prd', self.client_file, ['calendar'], 'testing', factory)
        self.assertTrue(first['clientConfigured'])
        self.assertEqual(first['clientRevision'], second['clientRevision'])
        self.assertEqual({'gmail': False, 'drive': False, 'calendar': True}, second['apiConfirmed'])
        self.assertEqual('testing', second['publishingStatus'])
        self.assertEqual(1, len(store.secrets))
        self.assertNotIn('owner', store.get('CLIENT#gogcli-proxy'))
        self.assertNotIn('SECRET-NEVER-PRINT', json.dumps(second))
        self.assertNotIn('client_id', json.dumps(second))

    def test_interrupted_write_has_no_false_readiness_then_retry_can_finish(self):
        from test_onboarding_lifecycle import MemoryStore
        store = MemoryStore()
        factory = self.session()
        with patch.object(operator, 'Store', return_value=store):
            with patch.object(store, 'put', side_effect=RuntimeError('write interrupted')):
                with self.assertRaises(RuntimeError):
                    operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', factory)
            self.assertIsNone(store.get('CLIENT#gogcli-proxy'))
            self.assertEqual(1, len(store.secrets))
            result = operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', factory)
        self.assertTrue(result['clientConfigured'])
        self.assertEqual(2, len(store.secrets))

    def test_stale_confirmation_cannot_mark_replacement_ready(self):
        from test_onboarding_lifecycle import MemoryStore
        store = MemoryStore()
        factory = self.session()
        original = store.confirm_project
        def replaced_during_review(client, changes):
            store.put({**client, 'revision': 'replacement', 'api_confirmed': {}, 'publishing_status': 'unknown'})
            original(client, changes)
        with patch.object(operator, 'Store', return_value=store), patch.object(store, 'confirm_project', side_effect=replaced_during_review):
            with self.assertRaises(operator.WorkflowError):
                operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', factory)
        self.assertEqual({}, store.get('CLIENT#gogcli-proxy')['api_confirmed'])

    def test_replacement_after_review_cannot_report_readiness_for_other_revision(self):
        from test_onboarding_lifecycle import MemoryStore
        store = MemoryStore()
        factory = self.session()
        original = store.confirm_project
        def replaced_after_review(client, changes):
            original(client, changes)
            store.put({**client, 'revision': 'replacement', 'api_confirmed': {}, 'publishing_status': 'unknown'})
        with patch.object(operator, 'Store', return_value=store), patch.object(store, 'confirm_project', side_effect=replaced_after_review):
            result = operator.configure(self.source, 'gogcli-prd', self.client_file, ['gmail'], 'testing', factory)
        self.assertNotEqual('replacement', result['clientRevision'])
        self.assertEqual({'gmail': True, 'drive': False, 'calendar': False}, result['apiConfirmed'])
        self.assertEqual('replacement', store.get('CLIENT#gogcli-proxy')['revision'])

    def test_cli_failure_and_json_parse_errors_do_not_print_secret(self):
        self.client_file.write_text('{"SECRET-NEVER-PRINT": malformed}')
        output = io.StringIO()
        with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
            result = operator.main(['--source', str(self.source), '--environment', 'gogcli-prd',
                '--client-json', str(self.client_file), '--confirmed-apis', 'gmail', '--publishing-status', 'testing'])
        self.assertEqual(1, result)
        self.assertNotIn('SECRET-NEVER-PRINT', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())


if __name__ == '__main__':
    unittest.main()
