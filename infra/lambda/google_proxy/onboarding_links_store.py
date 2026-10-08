"""Connection preparation and atomic browser binding under account generations."""
import secrets
import time

from errors import WorkflowError

RETENTION = 604800


def prepare_link(store, idem_pk, fingerprint):
    from onboarding_store import digest, new_id
    pk = 'LINKPREP#' + idem_pk.removeprefix('IDEM#')
    existing = store.get(pk)
    if existing:
        if existing.get('expires_at', 0) <= int(time.time()):
            raise WorkflowError(409, 'idempotency_expired', 'Use a new request key for a fresh connection link.')
        if existing['fingerprint'] != fingerprint:
            raise WorkflowError(409, 'idempotency_conflict', 'This key has different account choices.')
        return existing
    op_id, link_id, capability = new_id(), new_id('cl'), secrets.token_urlsafe(32)
    ref = store.prefix + '/onboarding/attempts/' + op_id + '/connection-link'
    # Track artifacts before writing them; interrupted, unaccepted preparation is bounded.
    store.put({'pk': 'LINKCLEANUP#' + op_id, 'connection_link_ref': ref,
               'expires_at': int(time.time()) + RETENTION}, absent=True)
    store.secret_put('attempts/' + op_id + '/connection-link', {'capability': capability})
    item = {'pk': pk, 'operation_id': op_id, 'connection_link_id': link_id,
            'connection_link_ref': ref, 'capability_hash': digest(capability),
            'fingerprint': fingerprint, 'expires_at': int(time.time()) + RETENTION}
    try:
        store.put(item, absent=True)
        return item
    except WorkflowError:
        store.secret_delete(ref)
        existing = store.get(pk)
        if not existing or existing['fingerprint'] != fingerprint:
            raise
        if existing.get('expires_at', 0) <= int(time.time()):
            raise WorkflowError(409, 'idempotency_expired', 'Use a new request key for a fresh connection link.')
        return existing


class ConnectionStore:
    def connection_accept_actions(self, operation, idem):
        deadline = operation['created_at'] + RETENTION
        actions = [{'Put': {'TableName': self.table.name, 'Item': {
            'pk': 'LINK#' + operation['connection_link_id'], 'operation_id': operation['id'],
            'capability_hash': operation['capability_hash'], 'expires_at': deadline},
            'ConditionExpression': 'attribute_not_exists(pk)'}}]
        prep_key = 'LINKPREP#' + idem['pk'].removeprefix('IDEM#')
        for key in (prep_key, 'LINKCLEANUP#' + operation['id']):
            actions.append({'Update': {'TableName': self.table.name, 'Key': {'pk': key},
                'UpdateExpression': 'SET expires_at = :expires',
                'ConditionExpression': 'connection_link_ref = :ref AND expires_at > :now',
                'ExpressionAttributeValues': {':expires': deadline, ':now': operation['created_at'],
                    ':ref': operation['connection_link_ref']}}})
        return actions

    def _connection_transaction(self, op, changes, now, binding=None, cancel=False):
        names, values = {'#p': 'phase', '#g': 'generation'}, {
            ':p': op['phase'], ':g': op['generation'], ':op': op['id']}
        checks = '#p = :p'
        if binding is not None:
            checks += ' AND attribute_not_exists(session_hash) AND link_expires_at > :now'
            values[':now'] = now
        else:
            checks += ' AND session_hash = :session AND link_expires_at > :now' if not cancel else ''
            if not cancel:
                values[':session'] = op['session_hash']
                values[':now'] = now
        updated = {**op, **changes, 'updated_at': now}
        actions = [{'Put': {'TableName': self.table.name, 'Item': updated,
            'ConditionExpression': checks, 'ExpressionAttributeNames': {'#p': 'phase'},
            'ExpressionAttributeValues': {key: value for key, value in values.items() if key not in {':g', ':op'}}}}]
        account_check = '#g = :g AND active_operation = :op'
        if cancel:
            account_changes = {'generation': op['generation'] + 1, 'latest_operation': {
                'id': op['id'], 'kind': op['kind'], 'phase': 'canceled', 'status': 'canceled'}}
            expression, update_names, update_values = self.update_parts(account_changes)
            actions.append({'Update': {'TableName': self.table.name, 'Key': {'pk': 'ACCOUNT#' + op['email']},
                'ConditionExpression': account_check, 'UpdateExpression': expression + ' REMOVE active_operation',
                'ExpressionAttributeNames': {**update_names, '#g': 'generation'},
                'ExpressionAttributeValues': {**update_values, ':g': op['generation'], ':op': op['id']}}})
        else:
            actions.append({'ConditionCheck': {'TableName': self.table.name, 'Key': {'pk': 'ACCOUNT#' + op['email']},
                'ConditionExpression': account_check, 'ExpressionAttributeNames': {'#g': 'generation'},
                'ExpressionAttributeValues': {':g': op['generation'], ':op': op['id']}}})
        self.transaction(actions)
        return updated

    def bind_connection(self, op, session_hash, now):
        if op['phase'] != 'awaiting_connection':
            raise WorkflowError(409, 'operation_conflict', 'Connection link is no longer available.')
        return self._connection_transaction(op, {'session_hash': session_hash}, now, binding=session_hash)

    def start_connection(self, op, key, now):
        return self._connection_transaction(op, {'phase': 'preparing_consent',
            'connection_consent_key': key, 'next_attempt': now + 120}, now)

    def cancel_connection(self, op, now):
        return self._connection_transaction(op, {'phase': 'canceled', 'status': 'canceled',
            'error_code': 'connection_link_canceled', 'expires_at': now + RETENTION}, now, cancel=True)
