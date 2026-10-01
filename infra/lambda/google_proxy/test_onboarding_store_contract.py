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

    def test_connection_link_acceptance_hash_lookup_is_atomic_with_generation_and_key(self):
        op = {**self.operation, 'connection_link_id': 'cl_link', 'capability_hash': 'hashed-secret',
              'connection_link_ref': '/test/onboarding/attempts/op_test/connection-link'}
        idem = {'pk': 'IDEM#key', 'fingerprint': 'request', 'operation_id': op['id']}
        wire = self.capture(lambda: self.store.accept(self.account, op, idem, {}))
        self.assertEqual(6, len(wire['TransactItems']))
        link = wire['TransactItems'][3]['Put']
        self.assertEqual({'S': 'LINK#cl_link'}, link['Item']['pk'])
        self.assertEqual({'S': 'hashed-secret'}, link['Item']['capability_hash'])
        self.assertEqual('attribute_not_exists(pk)', link['ConditionExpression'])
        for action in wire['TransactItems'][4:]:
            update = action['Update']
            self.assertEqual({'N': '604900'}, update['ExpressionAttributeValues'][':expires'])
            self.assertIn('expires_at > :now', update['ConditionExpression'])
        self.assertEqual({'S': 'LINKPREP#key'}, wire['TransactItems'][4]['Update']['Key']['pk'])
        self.assertEqual({'S': 'LINKCLEANUP#op_test'}, wire['TransactItems'][5]['Update']['Key']['pk'])

    def test_connection_claim_atomically_fences_first_binding_expiry_and_account(self):
        op = {**self.operation, 'phase': 'awaiting_connection', 'link_expires_at': 300}
        wire = self.capture(lambda: self.store.bind_connection(op, 'session-hash', 100))
        put, account = wire['TransactItems']
        self.assertIn('attribute_not_exists(session_hash)', put['Put']['ConditionExpression'])
        self.assertIn('link_expires_at > :now', put['Put']['ConditionExpression'])
        self.assertEqual({'S': 'session-hash'}, put['Put']['Item']['session_hash'])
        self.assertEqual('#g = :g AND active_operation = :op', account['ConditionCheck']['ConditionExpression'])

    def test_connection_continue_is_conditional_on_binding_and_waiting_phase(self):
        op = {**self.operation, 'phase': 'awaiting_connection', 'session_hash': 'binding'}
        wire = self.capture(lambda: self.store.start_connection(op, 'continue', 100))
        put = wire['TransactItems'][0]['Put']
        self.assertIn('session_hash = :session', put['ConditionExpression'])
        self.assertEqual({'S': 'awaiting_connection'}, put['ExpressionAttributeValues'][':p'])
        self.assertEqual({'S': 'preparing_consent'}, put['Item']['phase'])

    def test_connection_cancel_invalidates_generation_in_same_phase_fenced_transaction(self):
        op = {**self.operation, 'phase': 'authorizing', 'kind': 'enrollment'}
        wire = self.capture(lambda: self.store.cancel_connection(op, 100))
        put, update = wire['TransactItems']
        self.assertEqual({'S': 'authorizing'}, put['Put']['ExpressionAttributeValues'][':p'])
        self.assertEqual({'S': 'canceled'}, put['Put']['Item']['phase'])
        self.assertIn('REMOVE active_operation', update['Update']['UpdateExpression'])
        self.assertIn({'N': '3'}, update['Update']['ExpressionAttributeValues'].values())

    def test_enrollment_replacement_cannot_cancel_an_exchange_that_won_the_race(self):
        from onboarding_store import WorkflowError
        old = {**self.operation, 'pk': 'OP#old', 'id': 'old', 'phase': 'exchanging'}
        account = {**self.account, 'active_operation': 'old'}
        replacement = {**self.operation, 'kind': 'enrollment'}
        with patch.object(self.store, 'get', return_value=old), self.assertRaises(WorkflowError):
            self.store.accept(account, replacement, None, {}, supersede=True)
        self.assertIsNone(self.wire)
