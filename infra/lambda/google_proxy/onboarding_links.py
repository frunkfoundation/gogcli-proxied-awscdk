"""One frozen enrollment capability; target profiles never acquire owner authority."""
import secrets
import time

from onboarding_operations import application_project_id, email_address
from onboarding_services import selected_services
from onboarding_store import WorkflowError, digest


class ConnectionLinks:
    def __init__(self, service, base_url):
        self.service, self.store, self.base_url = service, service.store, base_url.rstrip('/')

    @staticmethod
    def csrf(operation_id, session_id):
        return digest('connection-csrf:' + session_id + ':' + operation_id)

    def claim_operation(self, link_id):
        link = self.store.get('LINK#' + str(link_id))
        return link.get('operation_id') if link else None

    def callback_state(self, state):
        row = self.store.get('STATE#' + digest(str(state)))
        op = self.store.get('OP#' + row['operation_id']) if row else None
        return op if op and op.get('state_hash') == digest(str(state)) else None

    def metadata(self, owner, link_id):
        op_id = self.claim_operation(link_id)
        op = self.store.get('OP#' + op_id) if op_id else None
        if not op or op.get('owner') != owner:
            raise WorkflowError(404, 'not_found', 'Connection link not found.')
        return self._owner(op)

    def _owner(self, op, capability=None):
        now = int(time.time())
        account = self.store.get('ACCOUNT#' + op['email']) or {}
        usable = op['phase'] in {'awaiting_connection', 'preparing_consent', 'authorizing'} and op['link_expires_at'] > now and account.get('generation') == op['generation']
        result = {'id': op['connection_link_id'], 'operationId': op['id'], 'email': op['email'],
                  'services': op['services'], 'expiresAt': op['link_expires_at'],
                  'status': op['status'], 'phase': op['phase'],
                  'operationUrl': self.base_url + '/v1/operations/' + op['id']}
        if capability and usable:
            result['connectionUrl'] = self.base_url + '/connect#' + op['connection_link_id'] + '.' + capability
        return result

    def mint(self, owner, email, key, body):
        email = email_address(email)
        pk, fingerprint, replay = self.service.replay(owner, email, 'connection-link', key, body)
        if replay:
            op = self.store.get('OP#' + replay['id'])
            saved = self.store.optional_secret(op['connection_link_ref'])
            return self._owner(op, saved['capability'] if saved else None)
        if set(body) - {'services', 'replace'} or ('replace' in body and not isinstance(body['replace'], bool)):
            raise WorkflowError(400, 'invalid_connection_link', 'Supply only services and a replacement choice.')
        account = self.service.account(owner, email)
        if account.get('connection_status') == 'disconnecting':
            raise WorkflowError(409, 'disconnect_in_progress', 'Wait for account removal.')
        services = selected_services(body.get('services', account.get('installed_services') or account['requested_services']))
        if not set(account.get('installed_services', [])).issubset(services):
            raise WorkflowError(409, 'scope_removal_requires_policy', 'Reconnect preserves installed services.')
        project = application_project_id()
        client = self.store.get('CLIENT#' + project)
        if not client or not client.get('secret_ref') or not client.get('revision') or any(not client.get('api_confirmed', {}).get(name) for name in services):
            raise WorkflowError(409, 'application_setup_required', 'The application is not ready to connect this account.')
        old = self.store.get('OP#' + account['active_operation']) if account.get('active_operation') else None
        if account.get('active_operation') and (not old or old['phase'] not in {'awaiting_connection', 'preparing_consent', 'authorizing'} or not body.get('replace')):
            raise WorkflowError(409, 'operation_conflict', 'Finish current work or explicitly replace its consent attempt.')
        prepared = self._prepare(pk, fingerprint)
        saved = self.store.secret_get(prepared['connection_link_ref'])
        now = int(time.time())
        op = self.service.accept(owner, account, 'enrollment', 'awaiting_connection',
            changes={'desired_connection': 'connected', 'requested_services': services},
            details={**{name: prepared[name] for name in ('connection_link_id', 'connection_link_ref', 'capability_hash')},
                'auth_mode': 'connection', 'client_revision': client['revision'], 'client_ref': client['secret_ref'],
                'project_id': project, 'services': services, 'owner_access_enabled': account.get('desired_owner_access', True),
                'link_expires_at': now + 86400, 'connection_session_expires_at': now + 90000,
                'next_attempt': now + 86400, 'command_key': key, 'command_request': body},
            idem=(pk, fingerprint), supersede=bool(old), operation_id=prepared['operation_id'])
        return self._owner(op, saved['capability'])

    def _prepare(self, pk, fingerprint):
        from onboarding_links_store import prepare_link
        return prepare_link(self.store, pk, fingerprint)

    def _bound(self, operation_id, session_id):
        op = self.store.get('OP#' + str(operation_id))
        if not session_id or not op or op.get('auth_mode') != 'connection' or op.get('session_hash') != digest(session_id) or op.get('connection_session_expires_at', 0) <= int(time.time()):
            raise WorkflowError(401, 'connection_session_required', 'Open this connection in its original browser profile.')
        account = self.store.get('ACCOUNT#' + op['email']) or {}
        if account.get('generation') != op['generation']:
            raise WorkflowError(409, 'operation_superseded', 'The owner replaced this connection attempt.')
        return op

    def projection(self, op):
        result = {name: op[name] for name in ('id', 'email', 'services', 'status', 'phase')}
        result['canContinue'] = ((op['phase'] == 'awaiting_connection' and op['link_expires_at'] > int(time.time())) or
                                 (op['phase'] == 'authorizing' and op.get('consent_expires_at', 0) > int(time.time())))
        result['expiresAt'] = op['link_expires_at']
        if op.get('consent_expires_at'):
            result['consentExpiresAt'] = op['consent_expires_at']
        if op.get('error_code'):
            result['errorCode'] = op['error_code']
            result['safeMessage'] = 'This connection could not complete. Ask the owner for a fresh link.'
        return result

    def operation(self, operation_id, session_id):
        return self.projection(self._bound(operation_id, session_id))

    def claim(self, link_id, capability, session_id):
        link = self.store.get('LINK#' + str(link_id))
        if not link or not isinstance(capability, str) or not secrets.compare_digest(link['capability_hash'], digest(capability)):
            raise WorkflowError(404, 'connection_link_unavailable', 'Connection link unavailable.')
        op = self.store.get('OP#' + link['operation_id'])
        if not op:
            raise WorkflowError(404, 'connection_link_unavailable', 'Connection link unavailable.')
        if op.get('session_hash'):
            try:
                op = self._bound(op['id'], session_id)
            except WorkflowError as error:
                if error.status == 401:
                    raise WorkflowError(409, 'connection_link_unavailable', 'Connection link is already bound to a browser profile.') from None
                raise
        else:
            if op['link_expires_at'] <= int(time.time()):
                raise WorkflowError(410, 'connection_link_expired', 'Ask the owner for a fresh connection link.')
            self.store.bind_connection(op, digest(session_id), int(time.time()))
            op = self._bound(op['id'], session_id)
        return {**self.projection(op), 'operationId': op['id']}

    def consent(self, operation_id, session_id, key):
        from onboarding_consent import consent_response, start_consent
        op = self._bound(operation_id, session_id)
        # Validate a stable command key before changing the reserved operation.
        self.service.replay(op['owner'], op['email'], 'connection-consent:' + op['id'], key, {})
        if op['phase'] == 'authorizing':
            response = consent_response(self.service, op, session_id)
            return {**self.projection(op), 'authorizationUrl': response['authorizationUrl']}
        if op.get('connection_consent_key'):
            if op['connection_consent_key'] != key:
                raise WorkflowError(409, 'idempotency_conflict', 'Use the original Continue request key.')
            if op['phase'] == 'preparing_consent':
                return self.projection(op)
            raise WorkflowError(409, 'consent_restart_required', 'Ask the owner for a fresh connection link.')
        if op['phase'] != 'awaiting_connection' or op['link_expires_at'] <= int(time.time()):
            raise WorkflowError(409, 'consent_restart_required', 'Ask the owner for a fresh connection link.')
        try:
            op = self.store.start_connection(op, key, int(time.time()))
        except WorkflowError:
            current = self._bound(operation_id, session_id)
            if current.get('connection_consent_key') == key and current['phase'] in {'preparing_consent', 'authorizing'}:
                return self.consent(operation_id, session_id, key)
            raise
        response = start_consent(self.service, op, session_id)
        return {**self.projection(self.store.get(op['pk'])), 'authorizationUrl': response['authorizationUrl']}

    def cancel(self, owner, link_id):
        self.metadata(owner, link_id)
        op = self.store.get('OP#' + self.claim_operation(link_id))
        if op['phase'] == 'canceled':
            return
        if op['phase'] not in {'awaiting_connection', 'preparing_consent', 'authorizing'}:
            raise WorkflowError(409, 'operation_conflict', 'Consent has already advanced; use owner account controls.')
        self.store.cancel_connection(op, int(time.time()))
