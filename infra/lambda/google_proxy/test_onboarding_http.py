import json
import unittest
from unittest.mock import patch

import onboarding
from onboarding_auth import AuthError, OwnerAuth


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
