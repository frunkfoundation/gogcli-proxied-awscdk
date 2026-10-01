"""Dedicated owner UI/API Lambda. Existing signed Gmail proxy stays separate."""
import base64
import json
import os
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from onboarding_auth import AuthError, OwnerAuth


@lru_cache(maxsize=1)
def get_auth():
    return OwnerAuth()


@lru_cache(maxsize=1)
def get_service():
    from onboarding_operations import build_service
    return build_service()


def base_url():
    return os.environ['ONBOARDING_BASE_URL'].rstrip('/') + '/'


def response(status, value, content_type='application/json', extra=None):
    headers = {'Content-Type': content_type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"}
    headers.update(extra or {})
    body = json.dumps(value, default=lambda item: int(item) if isinstance(item, Decimal) else str(item)) if content_type.endswith('json') else value
    return {'statusCode': status, 'headers': headers, 'body': body}


def problem(error, owner_login=True):
    value = {'type': 'urn:gogcli-proxy:onboarding:' + error.code, 'title': error.code.replace('_', ' '),
             'status': error.status, 'detail': error.detail}
    if error.status == 401 and owner_login:
        value['loginUrl'] = base_url() + 'auth/login'
    return response(error.status, value, 'application/problem+json')


def json_body(event):
    raw = event.get('body') or ''
    if len(raw) > 90000:
        raise AuthError(413, 'body_too_large', 'Upload JSON smaller than 64 KiB.')
    try:
        if event.get('isBase64Encoded'):
            raw = base64.b64decode(raw, validate=True).decode('utf-8')
        if len(raw.encode('utf-8')) > 65536:
            raise AuthError(413, 'body_too_large', 'Upload JSON smaller than 64 KiB.')
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise AuthError(400, 'invalid_json', 'Provide a JSON object.') from None
    if not isinstance(value, dict):
        raise AuthError(400, 'invalid_json', 'Provide a JSON object.')
    return value


def redirect(location, set_cookie=None):
    return response(303, '', 'text/plain', {'Location': location, **({'Set-Cookie': set_cookie} if set_cookie else {})})


def handler(event, context):
    if event.get('Records'):
        from onboarding_worker import handler as worker
        return worker(event, context)
    try:
        return dispatch(event)
    except AuthError as error:
        return problem(error, '/connection/v1/' not in event.get('path', event.get('rawPath', '')))
    except Exception as error:
        # Workflow exceptions expose safe public details; upstream response bodies never do.
        if all(hasattr(error, name) for name in ('status', 'code', 'detail')):
            return problem(error, '/connection/v1/' not in event.get('path', event.get('rawPath', '')))
        print(json.dumps({'event': 'onboarding_request_failed', 'error_type': type(error).__name__}))
        return problem(AuthError(500, 'internal_error', 'The request could not be completed. Reload to check its status.'))


def dispatch(event):
    method = event.get('httpMethod', event.get('requestContext', {}).get('http', {}).get('method', 'GET'))
    path = event.get('path', event.get('rawPath', ''))
    prefix = urlsplit(base_url()).path.rstrip('/')
    # REST API events may omit stage; HTTP API events can include it.
    if path == prefix or path.startswith(prefix + '/'):
        path = '/onboarding' + path[len(prefix):]
    headers = {key.lower(): value for key, value in (event.get('headers') or {}).items()}
    query = event.get('queryStringParameters') or {}
    assets = {'/onboarding/': ('index.html', 'text/html'), '/onboarding/app.js': ('app.js', 'text/javascript'),
              '/onboarding/style.css': ('style.css', 'text/css'),
              '/onboarding/connect': ('connect.html', 'text/html'),
              '/onboarding/connect/result': ('connect.html', 'text/html'),
              '/onboarding/connect.js': ('connect.js', 'text/javascript'),
              '/onboarding/about': ('about.html', 'text/html'), '/onboarding/privacy': ('privacy.html', 'text/html')}
    if method == 'GET' and path == '/onboarding':
        return redirect(base_url())
    if method == 'GET' and path in assets:
        filename, content_type = assets[path]
        asset = (Path(__file__).parent / 'assets' / 'onboarding' / filename).read_text()
        if filename == 'connect.html':
            asset = asset.replace('__ONBOARDING_BASE__', base_url())
        return response(200, asset, content_type)
    if method == 'GET' and path == '/onboarding/auth/login':
        location, set_cookie = get_auth().login()
        return redirect(location, set_cookie)
    if method == 'GET' and path == '/onboarding/auth/callback':
        return redirect(base_url(), get_auth().callback(query, headers))
    from onboarding_connection_http import google_callback, owner_dispatch, target_dispatch
    if method == 'GET' and path == '/onboarding/oauth/google/callback':
        return google_callback(query, headers)
    if path.startswith('/onboarding/connection/v1/'):
        return target_dispatch(event, method, path, headers)
    if not path.startswith('/onboarding/v1/'):
        raise AuthError(404, 'not_found', 'Route not found.')
    auth = get_auth()
    session = auth.session(headers)
    if method not in ('GET', 'HEAD'):
        auth.check_mutation(headers, session)
    owner = session['owner']
    result = owner_dispatch(event, method, path, headers, owner)
    if result is not None:
        return result
    service = get_service()
    if path == '/onboarding/v1/setup':
        if method == 'GET':
            data = service.setup(owner, session['session_id'])
            data['csrfToken'] = session['csrf']
            return response(200, data)
    match = re.fullmatch(r'/onboarding/v1/accounts/([^/]+)(/(enrollments|verifications|recoveries|access|connection))?', path)
    if match:
        email, _, action = match.groups()
        email = unquote(email)
        if method == 'PUT' and not action:
            existed = service.store.get('ACCOUNT#' + email.strip().lower())
            return response(200 if existed else 201, service.save_account(owner, email, json_body(event)),
                extra={'Location': base_url() + 'v1/accounts/' + email} if not existed else None)
        result = None
        if method == 'POST' and action in ('enrollments', 'verifications', 'recoveries'):
            key = headers.get('idempotency-key', '')
            body = json_body(event)
            if action == 'enrollments':
                result = service.enroll(owner, email, key, session['session_id'], body)
            elif action == 'verifications':
                result = service.verification(owner, email, key)
            else:
                result = service.recover(owner, email, key)
        elif method == 'PUT' and action == 'access':
            result = service.access(owner, email, json_body(event).get('enabled'))
        elif method == 'DELETE' and action == 'connection':
            result = service.disconnect(owner, email)
            if not result.get('id'):
                return response(204, '', 'text/plain')
        if result is not None:
            return response(202, result, extra={'Location': base_url() + 'v1/operations/' + result['id']})
    match = re.fullmatch(r'/onboarding/v1/operations/([A-Za-z0-9_-]+)', path)
    if match and method == 'GET':
        return response(200, service.operation(owner, match.group(1)))
    raise AuthError(404, 'not_found', 'Route not found.')
