import json
import unittest
from unittest.mock import Mock, patch

import onboarding
import onboarding_http
import onboarding_runtime as runtime
from onboarding_auth import AuthError, OwnerAuth
from onboarding_google import GoogleClient
from onboarding_operations import OnboardingService
from test_onboarding_lifecycle import MemoryStore
from onboarding_store import digest
from errors import WorkflowError


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
        with patch.object(runtime, 'get_auth') as factory, patch.object(runtime, 'get_service') as service:
            factory.return_value.session.side_effect = AuthError(401, 'login_required', 'Sign in.')
            response = onboarding.handler({'path': '/onboarding/v1/enrollments', 'httpMethod': 'POST'}, None)
            self.assertEqual(401, response['statusCode'])
            self.assertEqual('application/problem+json', response['headers']['Content-Type'])
            service.assert_not_called()

    def test_only_known_public_errors_expose_safe_details(self):
        upstream = type('UpstreamError', (Exception,), {'status': 409, 'code': 'private_provider_code',
                                                      'detail': 'private-provider-response'})()
        for error, status, detail in ((WorkflowError(409, 'operation_conflict', 'Finish current work.'),
                                      409, 'Finish current work.'), (upstream, 500, None)):
            with self.subTest(error=type(error).__name__), patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service') as service:
                auth.return_value.session.return_value = {'owner': 'owner', 'session_id': 'session', 'csrf': 'csrf'}
                service.return_value.setup.side_effect = error
                result = onboarding.handler({'path': '/onboarding/v1/setup', 'httpMethod': 'GET'}, None)
                self.assertEqual(status, result['statusCode'])
                self.assertEqual('application/problem+json', result['headers']['Content-Type'])
                if detail:
                    self.assertEqual(detail, json.loads(result['body'])['detail'])
                else:
                    self.assertEqual('internal_error', json.loads(result['body'])['type'].rsplit(':', 1)[-1])
                    self.assertNotIn('private', result['body'])

    def test_json_object_and_size_checked_before_workflow(self):
        for body in ('[]', '{', 'x' * 65537):
            with self.subTest(body=body[:5]), self.assertRaises(AuthError):
                onboarding_http.json_body({'body': body})

    def test_generated_stage_prefix_routes_to_fixed_assets(self):
        with patch.dict('os.environ', {'ONBOARDING_BASE_URL': 'https://api.example/prd/onboarding/'}):
            response = onboarding.handler({'path': '/prd/onboarding/style.css', 'httpMethod': 'GET'}, None)
            self.assertEqual(200, response['statusCode'])
            self.assertEqual('text/css', response['headers']['Content-Type'])
            self.assertEqual(404, onboarding.handler({'path': '/prd/onboarding/../requirements.txt', 'httpMethod': 'GET'}, None)['statusCode'])


    def test_public_home_and_privacy_need_no_session_or_workflow(self):
        with patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service') as service:
            for route, title in (('about', 'gogcli-proxy'), ('privacy', 'Privacy policy')):
                with self.subTest(route=route):
                    result = onboarding.handler({'path': '/prd/onboarding/' + route,
                        'httpMethod': 'GET'}, None)
                    self.assertEqual(200, result['statusCode'])
                    self.assertEqual('text/html', result['headers']['Content-Type'])
                    self.assertIn(title, result['body'])
                    self.assertIn('jay@kraecollective.com', result['body'])
                    self.assertEqual('no-store', result['headers']['Cache-Control'])
            auth.assert_not_called()
            service.assert_not_called()

    def test_removed_application_mutations_are_not_routes(self):
        with patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service') as service:
            auth.return_value.session.return_value = {'owner': 'owner', 'session_id': 'session'}
            for route in ('setup', 'projects/gogcli-proxy/oauth-client', 'projects/gogcli-proxy/setup'):
                result = onboarding.handler({'path': '/onboarding/v1/' + route,
                    'httpMethod': 'PUT', 'body': '{}'}, None)
                self.assertEqual(404, result['statusCode'])
            service.return_value.update_setup.assert_not_called()
            service.return_value.save_client.assert_not_called()
            service.return_value.update_project.assert_not_called()


class CallbackRecoveryTests(unittest.TestCase):
    setUp = BoundaryTests.setUp

    def owner_state(self, service):
        state, operation_id = 'private-state-' + 'x' * 32, 'op_' + '1' * 22
        store = MemoryStore()
        store.put({'pk': 'STATE#' + digest(state), 'operation_id': operation_id})
        store.put({'pk': 'OP#' + operation_id, 'id': operation_id,
                   'state_hash': digest(state), 'auth_mode': 'owner', 'phase': 'authorizing'})
        service.return_value.store = store
        return state

    def test_expired_google_callback_drops_code_and_preserves_workflow(self):
        with patch.object(runtime, 'get_auth') as factory, patch.object(runtime, 'get_service') as service:
            factory.return_value.session.side_effect = AuthError(401, 'login_required', 'Sign in.')
            state = self.owner_state(service)
            event = {'path': '/onboarding/oauth/google/callback', 'httpMethod': 'GET',
                     'queryStringParameters': {'code': 'private-google-code', 'state': state}}
            response = onboarding.handler(event, None)
            self.assertEqual(303, response['statusCode'])
            self.assertEqual('https://api.example/prd/onboarding/?recovery=owner-session-expired', response['headers']['Location'])
            self.assertNotIn('private-', str(response))
            service.return_value.callback.assert_not_called()

    def test_browser_callback_failure_redirects_to_saved_progress(self):
        with patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service') as service:
            auth.return_value.session.return_value = {'owner': 'jay', 'session_id': 'session'}
            service.return_value.callback.side_effect = AuthError(400, 'consent_expired', 'Start again.')
            state = self.owner_state(service)
            response = onboarding.handler({'path': '/onboarding/oauth/google/callback', 'httpMethod': 'GET',
                'queryStringParameters': {'code': 'private-code', 'state': state}}, None)
            self.assertEqual(303, response['statusCode'])
            self.assertEqual('https://api.example/prd/onboarding/?recovery=consent-incomplete', response['headers']['Location'])
            self.assertNotIn('private-', str(response))


class ClientImportTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.http = Mock()
        self.service = OnboardingService(self.store, GoogleClient(self.http),
            'https://app.example/onboarding/oauth/google/callback')
        self.document = {'web': {'client_id': 'example.apps.googleusercontent.com', 'project_id': 'gogcli-proxy',
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
            'client_secret': 'synthetic-secret', 'redirect_uris': [self.service.callback_url]}}
        env = patch.dict('os.environ', {'ONBOARDING_BASE_URL': 'https://app.example/onboarding/',
                                      'GOOGLE_PROJECT_ID': 'gogcli-proxy'})
        env.start()
        self.addCleanup(env.stop)

    def upload(self):
        try:
            return onboarding_http.response(200, self.service.import_client(self.document))
        except Exception as error:
            return onboarding_http.problem(error)

    def enroll_in_testing(self, api_confirmed):
        self.assertEqual(200, self.upload()['statusCode'])
        revision = self.store.get('CLIENT#gogcli-proxy')['revision']
        self.service.review_application( {'clientRevision': revision,
            'publishingStatus': 'testing', 'apiConfirmed': api_confirmed})
        self.service.save_account('owner', 'jay@example.com', {'services': ['drive']})
        with patch.object(runtime, 'get_auth') as auth, patch.object(runtime, 'get_service', return_value=self.service):
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
        self.assertFalse(self.store.get('CLIENT#gogcli-proxy')['publishing_status'] == 'production')
        self.http.post.assert_not_called()

    def test_testing_project_still_requires_selected_service_api_confirmation(self):
        result = self.enroll_in_testing({'gmail': True, 'drive': False})
        self.assertEqual(409, result['statusCode'], result['body'])
        self.assertEqual('application setup required', json.loads(result['body'])['title'])
        self.assertFalse(self.store.rows('OP#'))
        self.assertNotIn('active_operation', self.store.get('ACCOUNT#jay@example.com'))
        self.http.post.assert_not_called()

    def test_google_download_import_uses_canonical_runtime_endpoints(self):
        for endpoint in ('https://accounts.google.com/o/oauth2/auth', 'https://accounts.google.com/o/oauth2/v2/auth'):
            with self.subTest(endpoint=endpoint):
                self.document['web']['auth_uri'] = endpoint
                result = self.upload()
                self.assertEqual(200, result['statusCode'], result['body'])
                self.assertTrue(json.loads(result['body'])['clientConfigured'])
                self.assertNotIn('synthetic-secret', result['body'])
                client = self.store.secret_get(self.store.get('CLIENT#gogcli-proxy')['secret_ref'])
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
