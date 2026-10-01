"""Operation-scoped connection browser authority, separate from owner sessions."""
import hmac
import re
import secrets
from urllib.parse import unquote, urlencode, urlsplit

from onboarding_auth import AuthError, cookie, read_cookie

ID = r'[a-z]{2}_[1-9A-HJ-NP-Za-km-z]{22}'
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}')


def links():
    from onboarding import base_url, get_service
    from onboarding_links import ConnectionLinks
    return ConnectionLinks(get_service(), base_url())


def cookie_name(operation_id):
    return '__Host-gog-connect-' + operation_id


def scoped_session(headers, operation_id):
    token = read_cookie(headers, cookie_name(operation_id))
    if not TOKEN.fullmatch(token):
        raise AuthError(401, 'connection_session_required', 'Open this connection in the browser profile that claimed its link.')
    return token


def same_origin(headers):
    from onboarding import base_url
    base = urlsplit(base_url())
    if headers.get('origin') != base.scheme + '://' + base.netloc:
        raise AuthError(403, 'csrf_rejected', 'Open the connection page before continuing.')


def idempotency_key(headers):
    key = headers.get('idempotency-key', '')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
        raise AuthError(400, 'idempotency_key_required', 'Supply an Idempotency-Key of at most 128 letters, numbers, underscores, or hyphens.')
    return key


def scoped_projection(connection, value, session_id):
    from onboarding import base_url
    operation_id = value['id']
    return {**value, 'csrfToken': connection.csrf(operation_id, session_id),
            'resultUrl': base_url() + 'connect/result?' + urlencode({'operation': operation_id})}


def target_dispatch(event, method, path, headers):
    from onboarding import base_url, json_body, response
    if method == 'POST' and path == '/onboarding/connection/v1/claims':
        same_origin(headers)
        body = json_body(event)
        if set(body) != {'linkId', 'capability'} or not isinstance(body['linkId'], str) or not re.fullmatch(ID, body['linkId']) or not isinstance(body['capability'], str) or not TOKEN.fullmatch(body['capability']):
            raise AuthError(400, 'invalid_connection_link', 'Provide the complete connection link.')
        connection = links()
        operation_id = connection.claim_operation(body['linkId'])
        session_id = read_cookie(headers, cookie_name(operation_id)) if operation_id else ''
        if not TOKEN.fullmatch(session_id):
            session_id = secrets.token_urlsafe(32)
        value = connection.claim(body['linkId'], body['capability'], session_id)
        return response(200, scoped_projection(connection, value, session_id), extra={
            'Set-Cookie': cookie(cookie_name(value['id']), session_id, 90000)})
    match = re.fullmatch('/onboarding/connection/v1/operations/(' + ID + r')(/consents)?', path)
    if not match or (method, bool(match.group(2))) not in (('GET', False), ('POST', True)):
        raise AuthError(404, 'not_found', 'Route not found.')
    operation_id, action = match.groups()
    session_id = scoped_session(headers, operation_id)
    connection = links()
    if action:
        same_origin(headers)
        supplied = headers.get('x-csrf-token', '')
        if not supplied or not hmac.compare_digest(supplied, connection.csrf(operation_id, session_id)):
            raise AuthError(403, 'csrf_rejected', 'Reload the connection page before continuing.')
        key = idempotency_key(headers)
        if json_body(event):
            raise AuthError(400, 'invalid_consent', 'Continue does not accept account or service changes.')
        value = connection.consent(operation_id, session_id, key)
        return response(202, scoped_projection(connection, value, session_id), extra={
            'Location': base_url() + 'connection/v1/operations/' + operation_id})
    return response(200, scoped_projection(connection, connection.operation(operation_id, session_id), session_id))


def owner_dispatch(event, method, path, headers, owner):
    from onboarding import base_url, json_body, response
    match = re.fullmatch(r'/onboarding/v1/accounts/([^/]+)/connection-links', path)
    if match and method == 'POST':
        body = json_body(event)
        if set(body) - {'services', 'replace'}:
            raise AuthError(400, 'invalid_connection_link', 'Only services and replace can be specified.')
        value = links().mint(owner, unquote(match.group(1)), idempotency_key(headers), body)
        return response(201, value, extra={'Location': base_url() + 'v1/connection-links/' + value['id']})
    match = re.fullmatch('/onboarding/v1/connection-links/(' + ID + ')', path)
    if match and method == 'GET':
        return response(200, links().metadata(owner, match.group(1)))
    if match and method == 'DELETE':
        links().cancel(owner, match.group(1))
        return response(204, '', 'text/plain')
    return None


def google_callback(query, headers):
    from onboarding import base_url, get_auth, get_service, redirect
    failure = base_url() + 'connect/result?recovery=consent-incomplete'
    state = query.get('state', '')
    if not isinstance(state, str) or not TOKEN.fullmatch(state):
        return redirect(failure)
    connection = links()
    op = connection.callback_state(state)
    if not isinstance(op, dict):
        return redirect(failure)
    if op.get('auth_mode', 'owner') == 'connection':
        try:
            session_id = scoped_session(headers, op['id'])
            # Verify authority before disclosing an operation ID in the redirect.
            connection.operation(op['id'], session_id)
        except Exception as error:
            if isinstance(error, AuthError) or all(hasattr(error, name) for name in ('status', 'code', 'detail')):
                return redirect(failure)
            raise
        location = base_url() + 'connect/result?' + urlencode({'operation': op['id']})
        try:
            get_service().callback(state, query.get('code', ''), session_id, error=query.get('error'))
        except Exception as error:
            # Saved progress remains readable, including a failed or interrupted exchange.
            print('{"event":"connection_callback_failed","error_type":"' + type(error).__name__ + '"}')
        return redirect(location)
    if op.get('auth_mode', 'owner') != 'owner':
        return redirect(failure)
    try:
        session = get_auth().session(headers)
    except AuthError as error:
        if error.status == 401:
            return redirect(base_url() + '?recovery=owner-session-expired')
        raise
    try:
        value = get_service().callback(state, query.get('code', ''), session['session_id'], error=query.get('error'))
    except Exception as error:
        if all(hasattr(error, name) for name in ('status', 'code', 'detail')) and error.status < 500:
            return redirect(base_url() + '?recovery=consent-incomplete')
        raise
    return redirect(base_url() + '?' + urlencode({'operation': value.get('id', value.get('operation_id', ''))}))
