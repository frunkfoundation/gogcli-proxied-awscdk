"""Scoped browser contracts exercise the real dispatcher and workflow."""
import json
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

import onboarding
from onboarding_auth import OwnerAuth
import test_onboarding_links


class ConnectionHTTPTests(unittest.TestCase):
    def setUp(self):
        test_onboarding_links.ConnectionLinkTests.setUp(self)
        self.base = 'https://app.example/onboarding/'
        env = patch.dict('os.environ', {'ONBOARDING_BASE_URL': self.base})
        env.start()
        self.addCleanup(env.stop)
        auth = OwnerAuth.__new__(OwnerAuth)
        auth.origin = 'https://app.example'
        auth.session = lambda headers: ({'owner': 'owner', 'session_id': 'owner-session', 'csrf': 'owner-csrf'}
                                        if headers.get('cookie') == 'owner' else self.no_owner())
        factory = patch.object(onboarding, 'get_auth', return_value=auth)
        factory.start()
        self.addCleanup(factory.stop)
        service = patch.object(onboarding, 'get_service', return_value=self.service)
        service.start()
        self.addCleanup(service.stop)

    def no_owner(self):
        from onboarding_auth import AuthError
        raise AuthError(401, 'login_required', 'Sign in.')

    def request(self, route, method='GET', body=None, cookie='', csrf='', key='', origin='https://app.example', query=None):
        headers = {'Cookie': cookie, 'Origin': origin, 'X-CSRF-Token': csrf, 'Idempotency-Key': key}
        return onboarding.handler({'path': '/onboarding/' + route, 'httpMethod': method, 'headers': headers,
                                   'body': json.dumps(body) if body is not None else '',
                                   'queryStringParameters': query}, None)

    def mint(self, key='link'):
        result = self.request('v1/accounts/jay@example.com/connection-links', 'POST',
                              {'services': ['gmail']}, 'owner', 'owner-csrf', key)
        self.assertEqual(201, result['statusCode'], result['body'])
        data = json.loads(result['body'])
        link_id, capability = urlsplit(data['connectionUrl']).fragment.split('.')
        return data, {'linkId': link_id, 'capability': capability}

    def claim(self, payload, cookie=''):
        result = self.request('connection/v1/claims', 'POST', payload, cookie)
        self.assertEqual(200, result['statusCode'], result['body'])
        return json.loads(result['body']), result['headers'].get('Set-Cookie', cookie).split(';')[0]

    def test_claim_scopes_cookie_projection_and_retry(self):
        link, payload = self.mint()
        scoped, cookie = self.claim(payload)
        self.assertTrue(cookie.startswith('__Host-gog-connect-' + link['operationId'] + '='))
        response = self.request('connection/v1/claims', 'POST', payload, cookie)
        self.assertEqual(scoped, json.loads(response['body']))
        self.assertEqual(409, self.request('connection/v1/claims', 'POST', payload)['statusCode'])
        status = self.request('connection/v1/operations/' + scoped['id'], cookie=cookie)
        self.assertEqual(200, status['statusCode'])
        self.assertEqual('jay@example.com', json.loads(status['body'])['email'])
        for secret in ('owner', 'session_hash', 'client_ref', 'connectionUrl', payload['capability']):
            self.assertNotIn('"' + secret + '"', status['body'])
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(flag, response['headers'].get('Set-Cookie', ''))
        owner_api = self.request('v1/setup', cookie=cookie)
        self.assertEqual(401, owner_api['statusCode'])
        self.assertEqual(401, self.request('connection/v1/operations/' + scoped['id'])['statusCode'])
        self.assertNotIn('loginUrl', self.request('connection/v1/operations/' + scoped['id'])['body'])

    def test_mutation_bounds_origin_csrf_idempotency_and_frozen_body(self):
        _, payload = self.mint()
        scoped, cookie = self.claim(payload)
        route = 'connection/v1/operations/' + scoped['id'] + '/consents'
        for params in ({'origin': 'https://evil.example'}, {'origin': ''}, {'csrf': ''}):
            args = {'cookie': cookie, 'csrf': scoped['csrfToken'], 'key': 'continue', **params}
            self.assertEqual(403, self.request(route, 'POST', {}, **args)['statusCode'])
        self.assertEqual(400, self.request(route, 'POST', {}, cookie, scoped['csrfToken'])['statusCode'])
        self.assertEqual(400, self.request(route, 'POST', {'services': ['drive']}, cookie,
                                        scoped['csrfToken'], 'continue')['statusCode'])
        self.assertEqual(403, self.request('connection/v1/claims', 'POST', payload,
                                        origin='https://evil.example')['statusCode'])
        valid = self.request(route, 'POST', {}, cookie, scoped['csrfToken'], 'continue')
        self.assertEqual(202, valid['statusCode'], valid['body'])
        self.assertEqual(valid['body'], self.request(route, 'POST', {}, cookie, scoped['csrfToken'], 'continue')['body'])
        self.assertEqual(1, self.google.authorization_url.call_count)

    def test_callback_scoped_mode_never_owner_login_and_replay_no_exchange(self):
        _, payload = self.mint()
        scoped, cookie = self.claim(payload)
        route = 'connection/v1/operations/' + scoped['id'] + '/consents'
        self.assertEqual(202, self.request(route, 'POST', {}, cookie, scoped['csrfToken'], 'continue')['statusCode'])
        op = self.store.get('OP#' + scoped['id'])
        state = self.store.secret_get(op['attempt_ref'])['state']
        query = {'state': state, 'code': 'private-google-code'}
        missing = self.request('oauth/google/callback', query=query)
        self.assertEqual(303, missing['statusCode'])
        self.assertIn('connect/result?', missing['headers']['Location'])
        self.assertNotIn(scoped['id'], missing['headers']['Location'])
        self.google.exchange.assert_not_called()
        self.google.exchange.return_value = {'refresh_token': 'private-token'}
        success = self.request('oauth/google/callback', cookie=cookie, query=query)
        self.assertEqual(303, success['statusCode'])
        self.assertIn('connect/result?operation=' + scoped['id'], success['headers']['Location'])
        replay = self.request('oauth/google/callback', cookie=cookie, query=query)
        self.assertEqual(success['headers']['Location'], replay['headers']['Location'])
        self.assertEqual(1, self.google.exchange.call_count)
        self.assertNotIn('private-', str(success))
        unknown = self.request('oauth/google/callback', query={'state': 'unknown', 'code': 'private-code'})
        self.assertIn('connect/result?recovery=', unknown['headers']['Location'])
        self.assertNotIn('private-', str(unknown))

    def test_second_operation_cookie_cannot_read_or_consent_first(self):
        _, payload = self.mint()
        first, first_cookie = self.claim(payload)
        self.service.save_account('owner', 'other@example.com', {'services': ['gmail']})
        result = self.request('v1/accounts/other@example.com/connection-links', 'POST',
                              {'services': ['gmail']}, 'owner', 'owner-csrf', 'second')
        self.assertEqual(201, result['statusCode'])
        link, capability = urlsplit(json.loads(result['body'])['connectionUrl']).fragment.split('.')
        _, second_cookie = self.claim({'linkId': link, 'capability': capability})
        self.assertEqual(401, self.request('connection/v1/operations/' + first['id'], cookie=second_cookie)['statusCode'])
        self.assertEqual(200, self.request('connection/v1/operations/' + first['id'], cookie=first_cookie)['statusCode'])


if __name__ == '__main__':
    unittest.main()
