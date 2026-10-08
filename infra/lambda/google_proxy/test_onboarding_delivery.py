"""Delivery failure cannot abandon a live account mutation."""
import unittest
from unittest.mock import patch
import onboarding_worker as worker
import onboarding_runtime as runtime
from test_onboarding_lifecycle import MemoryStore


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.operation_id = 'op_' + '1' * 22
        self.op = {'pk': 'OP#' + self.operation_id, 'id': self.operation_id, 'email': 'a@example.com', 'generation': 1,
            'kind': 'enrollment', 'phase': 'pending_promotion', 'status': 'running', 'created_at': 10, 'next_attempt': 0}
        self.account = {'pk': 'ACCOUNT#a@example.com', 'email': 'a@example.com', 'generation': 1,
            'active_operation': self.operation_id, 'connection_status': 'connected', 'credential_revision': 'installed'}
        self.store.put(self.op)
        self.store.put(self.account)
        self.event = {'Records': [{'eventSource': 'aws:sqs', 'eventSourceARN': 'dlq', 'messageId': 'delivery', 'body': self.operation_id}]}

    def invoke(self):
        with patch.object(runtime, 'runtime_store', return_value=self.store), patch('time.time', return_value=100), patch.dict('os.environ', {'ONBOARDING_DLQ_ARN': 'dlq'}):
            return worker.handler(self.event, None)

    def assert_retry(self):
        self.assertEqual({'batchItemFailures': [{'itemIdentifier': 'delivery'}]}, self.invoke())
        self.assertEqual(self.operation_id, self.store.get(self.account['pk'])['active_operation'])
        self.assertEqual('pending_promotion', self.store.get(self.op['pk'])['phase'])

    def test_live_worker_claim_is_not_terminalized(self):
        self.store.put({**self.op, 'lease_owner': 'worker', 'lease_until': 150})
        self.assert_retry()

    def test_live_account_effect_lease_is_not_terminalized(self):
        self.store.put({**self.account, 'effect_lease_owner': 'worker', 'effect_lease_until': 150})
        self.assert_retry()

    def test_future_attempt_is_not_terminalized(self):
        self.store.put({**self.op, 'next_attempt': 150})
        self.assert_retry()

    def test_claim_race_keeps_delivery_retryable(self):
        original = self.store.claim_operation
        def race(operation, lease, now):
            self.store.put({**operation, 'lease_owner': 'other-worker', 'lease_until': now + 60})
            return original(operation, lease, now)
        self.store.claim_operation = race
        self.assert_retry()

    def test_idle_failed_delivery_is_claimed_and_terminalized_without_losing_connection(self):
        self.assertEqual({'batchItemFailures': []}, self.invoke())
        op, account = self.store.get(self.op['pk']), self.store.get(self.account['pk'])
        self.assertEqual('worker_delivery_failed', op['error_code'])
        self.assertEqual('failed', op['status'])
        self.assertNotIn('active_operation', account)
        self.assertEqual('installed', account['credential_revision'])
        self.assertEqual('connected', account['connection_status'])
