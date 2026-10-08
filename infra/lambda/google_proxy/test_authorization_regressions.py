import unittest
from unittest.mock import Mock, patch
import policy
import proxy_authorization as authorization
import proxy_config
from errors import ProxyError
import handler
from policy import _normalize_policy_document
from routing import resolve_google_host_and_path, RoutingError
from proxy_http import _build_query_string


class AuthorizationRegressions(unittest.TestCase):
    def doc(self, source, rules=(), default='deny'):
        return _normalize_policy_document({'default_effect': default, 'rules': list(rules)}, source=source, source_rank={'default': 0, 'caller': 1, 'account': 2}[source])

    def evaluate(self, *docs, email='a@example.com'):
        ctx = {'method': 'GET', 'host': 'gmail.googleapis.com', 'path': '/gmail/v1/users/me/messages', 'query_keys': [], 'account_email': email, 'account_domain': 'example.com', 'request_size_bytes': 0}
        return policy._evaluate_policy(policy._compose_policy_documents(docs), ctx)['effect']

    def test_account_allow_cannot_override_caller_deny(self):
        self.assertEqual('deny', self.evaluate(self.doc('default'), self.doc('caller', [{'effect': 'deny'}]), self.doc('account', [{'effect': 'allow', 'priority': 999}])))

    def test_narrow_caller_cannot_inherit_default_allow(self):
        self.assertEqual('deny', self.evaluate(self.doc('default', [{'effect': 'allow'}]), self.doc('caller', [{'effect': 'allow', 'match': {'path_regex': '^/labels$'}}])))

    def test_empty_global_deny_and_caller_allow(self):
        self.assertEqual('allow', self.evaluate(self.doc('default'), self.doc('caller', [{'effect': 'allow'}])))

    def test_account_default_deny_is_ceiling(self):
        self.assertEqual('deny', self.evaluate(self.doc('default'), self.doc('caller', [{'effect': 'allow'}]), self.doc('account')))

    def test_account_selector_does_not_spill(self):
        docs = [self.doc('default'), self.doc('caller', [{'effect': 'allow', 'match': {'accounts': ['a@example.com']}}])]
        self.assertEqual('allow', self.evaluate(*docs))
        self.assertEqual('deny', self.evaluate(*docs, email='b@example.com'))

    def test_dot_and_encoded_segments_rejected(self):
        for segment in ('..', '%2e%2e', '%252e%252e', '%2f', '%5c'):
            with self.subTest(segment=segment), self.assertRaises(RoutingError):
                resolve_google_host_and_path('/gmail/v1/users/me/labels/' + segment + '/messages', 'gmail.googleapis.com')

    def test_contradictory_query_representations_rejected(self):
        with self.assertRaises(ProxyError):
            _build_query_string({'queryStringParameters': {'q': 'one'}, 'multiValueQueryStringParameters': {'q': ['two']}})

    def test_policy_cache_ttl_cannot_extend_ceiling(self):
        with patch.dict('os.environ', {'GOG_POLICY_CACHE_TTL_SECONDS': '999'}):
            self.assertEqual(30, proxy_config._policy_cache_ttl_seconds())

    def test_ambiguous_headers_rejected_before_google(self):
        base = {'httpMethod': 'GET', 'path': '/gmail/v1/users/me/messages', 'requestContext': {'identity': {'userArn': 'arn:aws:iam::123456789012:role/agent'}}}
        for headers in ({'X-GOG-Account': 'a@example.com', 'x-gog-account': 'b@example.com'}, {'X-HTTP-Method-Override': 'DELETE'}, {'X-HTTP-Method-Override ': 'DELETE'}):
            with self.subTest(headers=headers), patch.object(handler, '_HTTP') as http:
                self.assertEqual(400, handler.handler(dict(base, headers=headers), None)['statusCode'])
                http.post.assert_not_called()
                http.send.assert_not_called()

    def test_warm_cache_access_removal_is_observed_at_ceiling(self):
        fake_ssm = Mock()
        fake_ssm.get_parameter.return_value = {'Parameter': {'Value': '{"emails":["a@example.com"]}'}}
        proxy_config._CACHE.clear()
        with patch.object(proxy_config, '_SSM', fake_ssm), patch('proxy_config.time.time', return_value=100):
            self.assertTrue(authorization._is_allowed(proxy_config._ssm_get_json('/allow', cache_ttl_seconds=999), 'a@example.com'))
        fake_ssm.get_parameter.return_value = {'Parameter': {'Value': '{"emails":[]}'}}
        with patch.object(proxy_config, '_SSM', fake_ssm), patch('proxy_config.time.time', return_value=159):
            self.assertTrue(authorization._is_allowed(proxy_config._ssm_get_json('/allow'), 'a@example.com'))
        with patch.object(proxy_config, '_SSM', fake_ssm), patch('proxy_config.time.time', return_value=160):
            self.assertFalse(authorization._is_allowed(proxy_config._ssm_get_json('/allow'), 'a@example.com'))
        proxy_config._CACHE.clear()

    def test_invalid_empty_policy_objects_are_not_unconstrained_allow(self):
        for invalid in ([], None, '', False):
            with self.subTest(value=invalid), self.assertRaises(ProxyError):
                self.doc('caller', [{'effect': 'allow', 'match': invalid}])

    def test_deny_wins_even_when_allow_has_higher_priority(self):
        self.assertEqual('deny', self.evaluate(self.doc('default'), self.doc('caller', [{'effect': 'allow', 'priority': 999}, {'effect': 'deny', 'priority': 0}])))

    def test_account_denylist_allows_unmatched_reads(self):
        self.assertEqual('allow', self.evaluate(self.doc('default'), self.doc('caller', [{'effect': 'allow'}]), self.doc('account', [{'effect': 'deny', 'match': {'methods': ['DELETE']}}], default='allow')))

    def test_cached_google_token_cannot_bypass_expired_authorization(self):
        import json
        import credentials
        credentials._TOKEN_CACHE.clear()
        proxy_config._CACHE.clear()
        allowed = ['a@example.com']
        caller_rules = [{'effect': 'allow'}]
        cfg = {'type': 'oauth_refresh', 'client_id': 'client', 'client_secret': 'secret', 'refresh_token': 'refresh'}
        fake_ssm = Mock()
        def get_parameter(Name, **kwargs):
            if '/allow/' in Name:
                value = {'emails': allowed}
            elif '/google/accounts/' in Name:
                value = cfg
            elif '/policy/caller/' in Name:
                value = {'default_effect': 'deny', 'rules': caller_rules}
            elif '/policy/account/' in Name:
                value = {'default_effect': 'allow', 'rules': []}
            else:
                value = {'default_effect': 'deny', 'rules': []}
            return {'Parameter': {'Value': json.dumps(value)}}
        fake_ssm.get_parameter.side_effect = get_parameter
        http = Mock()
        http.post.return_value = Mock(status_code=200, json=lambda: {'access_token': 'cached', 'expires_in': 3600})
        http.send.side_effect = lambda *args, **kwargs: Mock(status_code=200, headers={'Content-Type': 'application/json'}, iter_content=lambda **kwargs: iter([b'{}']))
        event = {'httpMethod': 'GET', 'path': '/gmail/v1/users/me/messages', 'headers': {'X-GOG-Account': 'a@example.com', 'X-GOG-Google-Host': 'gmail.googleapis.com'}, 'requestContext': {'identity': {'userArn': 'arn:aws:iam::123456789012:role/agent'}}}
        with patch.object(proxy_config, '_SSM', fake_ssm), patch.object(handler, '_HTTP', http), patch.dict('os.environ', {'GOG_POLICY_ENABLED': 'true'}):
            with patch('time.time', return_value=100):
                self.assertEqual(200, handler.handler(event, None)['statusCode'])
            caller_rules.clear()
            with patch('time.time', return_value=129):
                self.assertEqual(200, handler.handler(event, None)['statusCode'])
            with patch('time.time', return_value=130):
                self.assertEqual(403, handler.handler(event, None)['statusCode'])
            caller_rules.append({'effect': 'allow'})
            allowed.clear()
            with patch('time.time', return_value=160):
                self.assertEqual(403, handler.handler(event, None)['statusCode'])
        self.assertEqual(1, http.post.call_count)
        self.assertEqual(2, http.send.call_count)
        proxy_config._CACHE.clear()
        credentials._TOKEN_CACHE.clear()

    def test_blank_and_nonstring_selectors_fail_closed(self):
        for field in ('accounts', 'hosts', 'methods'):
            for value in ('', ' ', None, 1, {}):
                with self.subTest(field=field, value=value), self.assertRaises(ProxyError):
                    self.doc('caller', [{'effect': 'allow', 'match': {field: [value]}}])
        for email in ('missing-at', 'a@@example.com', 'a@example.com b@example.com', 'a@example.com/other'):
            with self.subTest(email=email), self.assertRaises(ProxyError):
                self.doc('caller', [{'effect': 'allow', 'match': {'accounts': [email]}}])

    def test_noninteger_numbers_fail_closed(self):
        for priority in (True, 1.5, '1'):
            with self.subTest(priority=priority), self.assertRaises(ProxyError):
                self.doc('caller', [{'effect': 'allow', 'priority': priority}])
