import json
import unittest
from unittest.mock import Mock, patch

import onboarding
from onboarding_auth import AuthError, OwnerAuth
from onboarding_google import GoogleClient
from onboarding_operations import OnboardingService
from test_onboarding_lifecycle import MemoryStore


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'ONBOARDING_BASE_URL': 'https://api.example/prd/onboarding/'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_mutations_require_both_csrf_and_same_origin(self):
        auth = OwnerAuth.__new__(OwnerAuth)
        auth.origin = 'https://api.example'
        session = {'csrf': 'correct'}
        for headers in ({'origin': auth.origin}, {'x-csrf-token': 'correct'},
                        {'origin': 'https://evil.example', 'x-csrf-token': 'correct'}):
            with self.subTest(headers=headers), self.assertRaises(AuthError):
                auth.check_mutation(headers, session)
        auth.check_mutation({'origin': auth.origin, 'x-csrf-token': 'correct'}, session)

    def test_unauthenticated_write_never_reaches_workflow(self):
        with patch.object(onboarding, 'get_auth') as factory, patch.object(onboarding, 'get_service') as service:
            factory.return_value.session.side_effect = AuthError(401, 'login_required', 'Sign in.')
            response = onboarding.handler({'path': '/onboarding/v1/enrollments', 'httpMethod': 'POST'}, None)
            self.assertEqual(401, response['statusCode'])
            self.assertEqual('application/problem+json', response['headers']['Content-Type'])
            service.assert_not_called()

    def test_json_object_and_size_checked_before_workflow(self):
        for body in ('[]', '{', 'x' * 65537):
            with self.subTest(body=body[:5]), self.assertRaises(AuthError):
                onboarding.json_body({'body': body})

    def test_generated_stage_prefix_routes_to_fixed_assets(self):
        with patch.dict('os.environ', {'ONBOARDING_BASE_URL': 'https://api.example/prd/onboarding/'}):
            response = onboarding.handler({'path': '/prd/onboarding/style.css', 'httpMethod': 'GET'}, None)
            self.assertEqual(200, response['statusCode'])
            self.assertEqual('text/css', response['headers']['Content-Type'])
            self.assertEqual(404, onboarding.handler({'path': '/prd/onboarding/../requirements.txt', 'httpMethod': 'GET'}, None)['statusCode'])


class CallbackRecoveryTests(unittest.TestCase):
    setUp = BoundaryTests.setUp

    def test_expired_google_callback_drops_code_and_preserves_workflow(self):
        with patch.object(onboarding, 'get_auth') as factory, patch.object(onboarding, 'get_service') as service:
            factory.return_value.session.side_effect = AuthError(401, 'login_required', 'Sign in.')
            event = {'path': '/onboarding/oauth/google/callback', 'httpMethod': 'GET',
                     'queryStringParameters': {'code': 'private-google-code', 'state': 'private-state'}}
            response = onboarding.handler(event, None)
            self.assertEqual(303, response['statusCode'])
            self.assertEqual('https://api.example/prd/onboarding/?recovery=owner-session-expired', response['headers']['Location'])
            self.assertNotIn('private-', str(response))
            service.assert_not_called()

    def test_browser_callback_failure_redirects_to_saved_progress(self):
        with patch.object(onboarding, 'get_auth') as auth, patch.object(onboarding, 'get_service') as service:
            auth.return_value.session.return_value = {'owner': 'jay', 'session_id': 'session'}
            service.return_value.callback.side_effect = AuthError(400, 'consent_expired', 'Start again.')
            response = onboarding.handler({'path': '/onboarding/oauth/google/callback', 'httpMethod': 'GET',
                'queryStringParameters': {'code': 'private-code', 'state': 'private-state'}}, None)
            self.assertEqual(303, response['statusCode'])
            self.assertEqual('https://api.example/prd/onboarding/?recovery=consent-incomplete', response['headers']['Location'])
            self.assertNotIn('private-', str(response))


class ClientImportTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.http = Mock()
        self.service = OnboardingService(self.store, GoogleClient(self.http),
            'https://app.example/onboarding/oauth/google/callback')
        self.document = {'web': {'client_id': 'example.apps.googleusercontent.com', 'project_id': 'project-one',
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
            'client_secret': 'synthetic-secret', 'redirect_uris': [self.service.callback_url]}}
        env = patch.dict('os.environ', {'ONBOARDING_BASE_URL': 'https://app.example/onboarding/',
                                      'GOOGLE_EXISTING_PROJECTS': '[]'})
        env.start()
        self.addCleanup(env.stop)

    def upload(self):
        with patch.object(onboarding, 'get_auth') as auth, patch.object(onboarding, 'get_service', return_value=self.service):
            auth.return_value.session.return_value = {'owner': 'owner', 'session_id': 'session'}
            return onboarding.handler({'path': '/onboarding/v1/projects/project-one/oauth-client',
                'httpMethod': 'PUT', 'body': json.dumps(self.document)}, None)

    def enroll_in_testing(self, api_confirmed):
        self.assertEqual(200, self.upload()['statusCode'])
        revision = self.store.get('CLIENT#project-one')['revision']
        self.service.update_project('owner', 'project-one', {'clientRevision': revision,
            'publishingConfirmed': False, 'apiConfirmed': api_confirmed})
        self.service.save_account('owner', 'jay@example.com', {'projectId': 'project-one', 'services': ['drive']})
        with patch.object(onboarding, 'get_auth') as auth, patch.object(onboarding, 'get_service', return_value=self.service):
            auth.return_value.session.return_value = {'owner': 'owner', 'session_id': 'session'}
            return onboarding.handler({'path': '/onboarding/v1/accounts/jay@example.com/enrollments',
                'httpMethod': 'POST', 'headers': {'Idempotency-Key': 'testing-consent'}, 'body': '{}'}, None)

    def test_testing_project_can_start_owner_google_consent(self):
        result = self.enroll_in_testing({'drive': True})
        self.assertEqual(202, result['statusCode'], result['body'])
        operation = json.loads(result['body'])
        self.assertEqual('authorizing', operation['phase'])
        self.assertTrue(operation['authorizationUrl'].startswith('https://accounts.google.com/o/oauth2/v2/auth?'))
        self.assertTrue(result['headers']['Location'].endswith('/v1/operations/' + operation['id']))
        self.assertEqual(operation['id'], self.store.get('ACCOUNT#jay@example.com')['active_operation'])
        self.assertFalse(self.store.get('CLIENT#project-one')['publishing_confirmed'])
        self.http.post.assert_not_called()

    def test_testing_project_still_requires_selected_service_api_confirmation(self):
        result = self.enroll_in_testing({'gmail': True, 'drive': False})
        self.assertEqual(409, result['statusCode'], result['body'])
        self.assertEqual('project setup required', json.loads(result['body'])['title'])
        self.assertFalse(self.store.rows('OP#'))
        self.assertNotIn('active_operation', self.store.get('ACCOUNT#jay@example.com'))
        self.http.post.assert_not_called()

    def test_google_download_import_uses_canonical_runtime_endpoints(self):
        for endpoint in ('https://accounts.google.com/o/oauth2/auth', 'https://accounts.google.com/o/oauth2/v2/auth'):
            with self.subTest(endpoint=endpoint):
                self.document['web']['auth_uri'] = endpoint
                result = self.upload()
                self.assertEqual(200, result['statusCode'], result['body'])
                self.assertTrue(next(p for p in json.loads(result['body'])['projects']
                                     if p['projectId'] == 'project-one')['clientConfigured'])
                self.assertNotIn('synthetic-secret', result['body'])
                client = self.store.secret_get(self.store.get('CLIENT#project-one')['secret_ref'])
                url = self.service.google.authorization_url(client, self.service.callback_url,
                    'state', 'verifier', 'nonce', 'jay@example.com', ['drive'])
                self.assertTrue(url.startswith('https://accounts.google.com/o/oauth2/v2/auth?'))
                self.http.post.return_value = Mock(status_code=200, json=lambda: {})
                self.service.google.exchange(client, self.service.callback_url, 'code', 'verifier')
                self.assertEqual('https://oauth2.googleapis.com/token', self.http.post.call_args.args[0])

    def test_untrusted_endpoint_import_is_rejected_without_saving(self):
        for key in ('auth_uri', 'token_uri'):
            original = self.document['web'][key]
            for endpoint in ('https://evil.example/oauth', 'http://accounts.google.com/o/oauth2/auth',
                             'https://accounts.google.com/o/oauth2/auth?redirect=evil', None, []):
                with self.subTest(key=key, endpoint=endpoint):
                    self.document['web'][key] = endpoint
                    result = self.upload()
                    self.assertEqual(400, result['statusCode'], result['body'])
                    self.assertEqual('invalid client', json.loads(result['body'])['title'])
                    self.assertFalse(self.store.secrets)
                    self.assertFalse(self.store.items)
            self.document['web'][key] = original
