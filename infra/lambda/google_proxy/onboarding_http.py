"""Shared browser HTTP responses and bounded JSON input."""
import base64
import json
import os
from decimal import Decimal

from errors import AuthError


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
