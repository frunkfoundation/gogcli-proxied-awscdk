import time
import unittest
from unittest.mock import Mock, patch

from authlib.jose import JsonWebKey, JsonWebToken
from onboarding_auth import AuthError, LOGIN_COOKIE, OwnerAuth, digest


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = JsonWebKey.generate_key('RSA', 2048, is_private=True)

    def auth(self):
        auth = OwnerAuth.__new__(OwnerAuth)
        auth.base_url = 'https://api.example/prd/onboarding/'
        auth.issuer = 'https://auth.example/application/o/app/'
        auth.group = 'owners'
        auth.table = Mock()
        auth.ssm = Mock()
        auth.ssm.get_parameter.return_value = {'Parameter': {'Value': '{"verifier":"test-verifier"}'}}
        auth.table.delete_item.return_value = {'Attributes': {'binding': digest('browser-binding'),
            'nonce': 'expected-nonce', 'secret_ref': '/temporary', 'expires_at': int(time.time()) + 60}}
        auth._config = Mock(return_value=({'token_endpoint': 'https://auth.example/token',
            'jwks_uri': 'https://auth.example/jwks'}, 'client-id', 'client-secret'))
        return auth

    def token(self, **changes):
        now = int(time.time())
        claims = {'iss': 'https://auth.example/application/o/app/', 'aud': 'client-id', 'sub': 'jay',
                  'iat': now, 'exp': now + 60, 'nonce': 'expected-nonce', 'groups': ['owners']}
        claims.update(changes)
        return JsonWebToken(['RS256']).encode({'alg': 'RS256'}, claims, self.key).decode()

    def exchange(self, auth, token):
        with patch('onboarding_auth.OAuth2Session') as client, patch('onboarding_auth.requests.get') as get:
            client.return_value.fetch_token.return_value = {'id_token': token, 'access_token': 'opaque'}
            get.return_value.json.return_value = self.key.as_dict(is_private=False)
            result = auth.callback({'state': 'state', 'code': 'code'}, {'cookie': LOGIN_COOKIE + '=browser-binding'})
            self.assertEqual('test-verifier', client.return_value.fetch_token.call_args.kwargs['code_verifier'])
            return result

    def test_verified_owner_gets_opaque_hash_only_session(self):
        auth = self.auth()
        result = self.exchange(auth, self.token())
        item = auth.table.put_item.call_args.kwargs['Item']
        self.assertEqual('jay', item['owner'])
        self.assertNotIn(result.split('=', 1)[1].split(';', 1)[0], str(item))
        self.assertIn('HttpOnly', result)
        self.assertIn('Secure', result)

    def test_issuer_audience_nonce_expiry_group_and_signature_rejected(self):
        cases = [{'iss': 'https://evil.example'}, {'aud': 'another-client'}, {'nonce': 'wrong'},
                 {'exp': 1}, {'groups': ['other']}]
        for changes in cases:
            with self.subTest(changes=changes):
                auth = self.auth()
                with self.assertRaises(AuthError):
                    self.exchange(auth, self.token(**changes))
                auth.table.put_item.assert_not_called()
        auth = self.auth()
        other_key = JsonWebKey.generate_key('RSA', 2048, is_private=True)
        token = JsonWebToken(['RS256']).encode({'alg': 'RS256'}, {'sub': 'jay'}, other_key).decode()
        with self.assertRaises(AuthError):
            self.exchange(auth, token)
        auth.table.put_item.assert_not_called()

    def test_replay_and_wrong_browser_never_exchange_code(self):
        for item in (None, {'binding': digest('different'), 'expires_at': int(time.time()) + 60}):
            auth = self.auth()
            auth.table.delete_item.return_value = {'Attributes': item}
            with patch('onboarding_auth.OAuth2Session') as client, self.assertRaises(AuthError):
                auth.callback({'state': 'state', 'code': 'code'}, {'cookie': LOGIN_COOKIE + '=browser-binding'})
            client.assert_not_called()

    def test_session_expiry_is_enforced_before_dynamo_ttl_deletion(self):
        auth = self.auth()
        auth.table.get_item.return_value = {'Item': {'expires_at': 1}}
        with self.assertRaises(AuthError):
            auth.session({'cookie': '__Host-gog-owner=' + 'a' * 43})
