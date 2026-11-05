import json
import unittest
from unittest.mock import Mock, patch
import proxy_config
import handler
import inventory


class InventoryTests(unittest.TestCase):
    def row(self, email):
        return {'pk': 'ACCOUNT#' + email, 'email': email, 'credential_revision': 'revision', 'connection_status': 'connected', 'installed_services': ['gmail'], 'granted_scopes': ['https://www.googleapis.com/auth/gmail.modify'], 'refresh_token': 'must-not-leak', 'selected_project_id': 'owner-only'}

    def invoke(self, allow=None, **overrides):
        event = {'httpMethod': 'GET', 'path': '/proxy/v1/accounts', 'requestContext': {'identity': {'userArn': 'arn:aws:iam::123456789012:role/agent'}}}
        event.update(overrides)
        with patch.object(proxy_config, '_ssm_get_json', return_value=allow), patch.object(handler, '_HTTP') as http:
            response = handler.handler(event, None)
            http.post.assert_not_called()
            http.send.assert_not_called()
        return response

    def test_explicit_inventory_fetches_only_granted_rows_and_no_secret_fields(self):
        table = Mock()
        table.get_item.return_value = {'Item': self.row('a@example.com')}
        with patch.object(inventory, 'metadata_table', return_value=table), patch.dict('os.environ', {'ONBOARDING_SETUP_URL': 'https://setup.example/'}):
            response = self.invoke({'emails': ['a@example.com']})
        self.assertEqual(200, response['statusCode'])
        payload = json.loads(response['body'])
        self.assertEqual('a@example.com', payload['accounts'][0]['email'])
        self.assertEqual('https://setup.example/', payload['setup_url'])
        self.assertNotIn('must-not-leak', response['body'])
        self.assertNotIn('owner-only', response['body'])
        table.scan.assert_not_called()
        table.get_item.assert_called_once_with(Key={'pk': 'ACCOUNT#a@example.com'}, ConsistentRead=True)

    def test_domain_scan_is_paginated_and_filtered(self):
        table = Mock()
        table.scan.side_effect = [{'Items': [self.row('hidden@other.com')], 'LastEvaluatedKey': {'pk': 'next'}}, {'Items': [self.row('a@example.com')]}]
        with patch.object(inventory, 'metadata_table', return_value=table):
            response = self.invoke({'domains': ['example.com']})
        self.assertEqual(['a@example.com'], [row['email'] for row in json.loads(response['body'])['accounts']])
        self.assertEqual(2, table.scan.call_count)

    def test_missing_permission_denies_before_metadata(self):
        with patch.object(inventory, 'metadata_table') as table:
            self.assertEqual(403, self.invoke()['statusCode'])
            table.assert_not_called()

    def test_empty_allowlist_returns_empty_inventory(self):
        with patch.object(inventory, 'metadata_table'):
            self.assertEqual([], json.loads(self.invoke({'emails': []})['body'])['accounts'])

    def test_reserved_route_never_requires_google_headers(self):
        self.assertEqual(404, self.invoke({'emails': []}, path='/proxy/v1/unknown')['statusCode'])
        self.assertEqual(405, self.invoke({'emails': []}, httpMethod='POST')['statusCode'])

    def test_malformed_permission_fails_closed(self):
        with patch.object(inventory, 'metadata_table') as table:
            self.assertEqual(500, self.invoke({'domains': '*'})['statusCode'])
            table.assert_not_called()
