"""Real boto3 document serialization and concurrency preconditions, no AWS calls."""
import json
import unittest
from unittest.mock import Mock, patch
import boto3
from onboarding_store import Store


class CapturedRequest(Exception):
    pass


class StoreContractTests(unittest.TestCase):
    def setUp(self):
        self.resource = boto3.resource('dynamodb', region_name='us-east-2', aws_access_key_id='testing', aws_secret_access_key='testing', endpoint_url='https://dynamodb.invalid')
        self.store = Store(self.resource.Table('metadata'), Mock(), '/test')
        self.wire = None
        def capture(params, **kwargs):
            self.wire = json.loads(params['body'])
            raise CapturedRequest()
        for name in ('TransactWriteItems', 'PutItem', 'UpdateItem'):
            self.resource.meta.client.meta.events.register('before-call.dynamodb.' + name, capture)
        self.account = {'pk': 'ACCOUNT#a@example.com', 'email': 'a@example.com', 'generation': 1}
        self.operation = {'pk': 'OP#op_test', 'id': 'op_test', 'email': 'a@example.com', 'generation': 2, 'phase': 'removing', 'created_at': 100}

    def capture(self, action):
        with self.assertRaises(CapturedRequest):
            action()
        return self.wire

    def test_accept_serializes_operation_account_and_idempotency_atomically(self):
        idem = {'pk': 'IDEM#key', 'fingerprint': 'request', 'operation_id': self.operation['id']}
        wire = self.capture(lambda: self.store.accept(self.account, self.operation, idem, {'desired_owner_access': False}))
        self.assertEqual(3, len(wire['TransactItems']))
        op, account, key = wire['TransactItems']
        self.assertEqual({'N': '2'}, op['Put']['Item']['generation'])
        self.assertEqual({'S': 'OP#op_test'}, op['Put']['Item']['pk'])
        self.assertIn('attribute_not_exists(active_operation)', account['Update']['ConditionExpression'])
        self.assertIn({'BOOL': False}, account['Update']['ExpressionAttributeValues'].values())
        self.assertEqual({'S': 'IDEM#key'}, key['Put']['Item']['pk'])

    def test_supersede_requires_old_active_pointer_and_old_operation_phase(self):
        old = {**self.operation, 'pk': 'OP#old', 'id': 'old', 'phase': 'verifying', 'idempotency_pk': 'IDEM#old'}
        account = {**self.account, 'active_operation': 'old'}
        with patch.object(self.store, 'get', side_effect=lambda key: old if key == 'OP#old' else {'pk': key}):
            wire = self.capture(lambda: self.store.accept(account, self.operation, None, {}, supersede=True))
        update = wire['TransactItems'][1]['Update']
        self.assertIn('active_operation = :oldop', update['ConditionExpression'])
        self.assertEqual({'S': 'old'}, update['ExpressionAttributeValues'][':oldop'])
        cancel = wire['TransactItems'][2]['Put']
        self.assertEqual({'S': 'verifying'}, cancel['ExpressionAttributeValues'][':phase'])
        self.assertEqual({'S': 'canceled'}, cancel['Item']['phase'])

    def test_terminal_requires_claimed_lease_and_retains_replay_result(self):
        op = {**self.operation, 'lease_owner': 'claim', 'lease_until': 160, 'idempotency_pk': 'IDEM#key'}
        account = {**self.account, 'generation': 2, 'active_operation': 'op_test'}
        with patch.object(self.store, 'get', side_effect=lambda key: account if key.startswith('ACCOUNT#') else {'pk': key}):
            wire = self.capture(lambda: self.store.terminal(op, {'phase': 'disconnected', 'status': 'succeeded'}, 200))
        terminal, idem, projection = wire['TransactItems']
        self.assertIn('lease_owner = :lease', terminal['Put']['ConditionExpression'])
        self.assertEqual({'S': 'claim'}, terminal['Put']['ExpressionAttributeValues'][':lease'])
        self.assertNotIn('lease_owner', terminal['Put']['Item'])
        self.assertEqual({'S': 'succeeded'}, idem['Put']['Item']['result']['M']['status'])
        self.assertIn('active_operation = :op', projection['Update']['ConditionExpression'])

    def test_metadata_save_cannot_overwrite_accepted_command(self):
        with patch.object(self.store, 'get', return_value=self.account):
            wire = self.capture(lambda: self.store.save_account_metadata(self.account, {**self.account, 'requested_services': ['gmail']}))
        self.assertIn('attribute_not_exists(active_operation)', wire['ConditionExpression'])
        self.assertEqual({'N': '1'}, wire['ExpressionAttributeValues'][':g'])

    def test_project_confirmation_is_conditional_on_current_revision(self):
        wire = self.capture(lambda: self.store.confirm_project({'pk': 'CLIENT#project', 'revision': 'old'}, {'publishing_confirmed': True}))
        self.assertEqual('#revision = :revision', wire['ConditionExpression'])
        self.assertEqual({'S': 'old'}, wire['ExpressionAttributeValues'][':revision'])
