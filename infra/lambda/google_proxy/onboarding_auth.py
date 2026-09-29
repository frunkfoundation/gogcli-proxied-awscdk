"""Owner OIDC login and opaque, server-side browser sessions."""
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import boto3
import requests
from botocore.exceptions import ClientError
from authlib.integrations.requests_client import OAuth2Session
from authlib.jose import JsonWebToken
from authlib.oidc.core import CodeIDToken

COOKIE = '__Host-gog-owner'
LOGIN_COOKIE = '__Host-gog-login'


class AuthError(Exception):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie(name, value, age):
    return f'{name}={value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={age}'


def read_cookie(headers, name):
    parsed = SimpleCookie()
    try:
        parsed.load(headers.get('cookie', ''))
        return parsed[name].value if name in parsed else ''
    except Exception:
        return ''


class OwnerAuth:
    def __init__(self):
        self.base_url = os.environ['ONBOARDING_BASE_URL'].rstrip('/') + '/'
        target = urlsplit(self.base_url)
        if target.scheme != 'https' or target.query or target.fragment:
            raise ValueError('ONBOARDING_BASE_URL must be a canonical HTTPS URL')
        self.origin = f'{target.scheme}://{target.netloc}'
        self.issuer = os.environ['ONBOARDING_OIDC_ISSUER']
        self.group = os.environ.get('ONBOARDING_OWNER_GROUP', 'gogcli-proxy-prd-owners')
        self.table = boto3.resource('dynamodb').Table(os.environ['ONBOARDING_TABLE'])
        self.ssm = boto3.client('ssm')

    def session(self, headers):
        token = read_cookie(headers, COOKIE)
        if len(token) < 32 or len(token) > 128:
            raise AuthError(401, 'login_required', 'Sign in to manage Gmail connections.')
        item = self.table.get_item(Key={'pk': 'auth-session#' + digest(token)}, ConsistentRead=True).get('Item')
        if not item or item['expires_at'] <= int(time.time()):
            raise AuthError(401, 'login_required', 'Sign in to manage Gmail connections.')
        return item

    def check_mutation(self, headers, session):
        supplied = headers.get('x-csrf-token', '')
        if headers.get('origin') != self.origin or not supplied or not hmac.compare_digest(supplied, session['csrf']):
            raise AuthError(403, 'csrf_rejected', 'Reload this page before making changes.')

    def _config(self):
        response = requests.get(self.issuer + '.well-known/openid-configuration', timeout=6)
        response.raise_for_status()
        metadata = response.json()
        if metadata['issuer'] != self.issuer:
            raise ValueError('OIDC discovery issuer mismatch')
        for endpoint in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            if urlsplit(metadata[endpoint]).scheme != 'https':
                raise ValueError('OIDC endpoint requires HTTPS')
        raw = self.ssm.get_parameter(Name=os.environ['ONBOARDING_OIDC_SECRET_PARAMETER'], WithDecryption=True)['Parameter']['Value']
        config = json.loads(raw)
        client_id = os.environ.get('ONBOARDING_OIDC_CLIENT_ID', config['client_id'])
        return metadata, client_id, config['client_secret']

    def login(self):
        metadata, client_id, client_secret = self._config()
        state, nonce, binding, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        client = OAuth2Session(client_id, client_secret, scope='openid profile email',
                               redirect_uri=self.base_url + 'auth/callback', code_challenge_method='S256')
        location, _ = client.create_authorization_url(metadata['authorization_endpoint'], state=state,
                                                      nonce=nonce, code_verifier=verifier)
        parameter = os.environ['GOG_SSM_PREFIX'].rstrip('/') + '/onboarding/owner-logins/' + digest(state)
        self.ssm.put_parameter(Name=parameter, Type='SecureString', Value=json.dumps({'verifier': verifier}), Overwrite=False)
        self.table.put_item(Item={'pk': 'auth-login#' + digest(state), 'binding': digest(binding),
                                  'nonce': nonce, 'secret_ref': parameter, 'expires_at': int(time.time()) + 600})
        return location, cookie(LOGIN_COOKIE, binding, 600)

    def callback(self, query, headers):
        state = query.get('state', '')
        binding = read_cookie(headers, LOGIN_COOKIE)
        if not state or len(state) > 128 or not binding:
            raise AuthError(400, 'invalid_login', 'Start a new sign-in from the setup page.')
        # Consume before exchange: a retry must never re-exchange an ambiguous code.
        try:
            item = self.table.delete_item(Key={'pk': 'auth-login#' + digest(state)}, ReturnValues='ALL_OLD',
                ConditionExpression='binding = :binding AND expires_at > :now',
                ExpressionAttributeValues={':binding': digest(binding), ':now': int(time.time())}).get('Attributes')
        except ClientError as error:
            if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise
            raise AuthError(400, 'invalid_login', 'This sign-in expired or was already used.') from None
        if not item or item['expires_at'] <= int(time.time()) or not hmac.compare_digest(item['binding'], digest(binding)):
            raise AuthError(400, 'invalid_login', 'This sign-in expired or was already used.')
        try:
            verifier = json.loads(self.ssm.get_parameter(Name=item['secret_ref'], WithDecryption=True)['Parameter']['Value'])['verifier']
        finally:
            self.ssm.delete_parameter(Name=item['secret_ref'])
        if query.get('error') or not query.get('code'):
            raise AuthError(400, 'login_cancelled', 'Sign-in was cancelled. Start again from the setup page.')
        metadata, client_id, client_secret = self._config()
        client = OAuth2Session(client_id, client_secret, redirect_uri=self.base_url + 'auth/callback')
        try:
            token = client.fetch_token(metadata['token_endpoint'], code=query['code'], code_verifier=verifier, timeout=6)
            response = requests.get(metadata['jwks_uri'], timeout=6)
            response.raise_for_status()
            claims = JsonWebToken(['RS256']).decode(token['id_token'], response.json(), claims_cls=CodeIDToken,
                claims_options={'iss': {'essential': True, 'value': self.issuer},
                                'aud': {'essential': True, 'value': client_id},
                                'sub': {'essential': True}, 'exp': {'essential': True}, 'iat': {'essential': True}},
                claims_params={'nonce': item['nonce'], 'client_id': client_id, 'access_token': token['access_token']})
            claims.validate(leeway=30)
        except Exception as error:
            raise AuthError(401, 'invalid_identity', 'Sign-in could not be verified. Start a new sign-in.') from error
        groups = claims.get('groups', [])
        if not isinstance(groups, list) or self.group not in groups:
            raise AuthError(403, 'owner_required', 'This page is restricted to the application owner.')
        session_token = secrets.token_urlsafe(32)
        self.table.put_item(Item={'pk': 'auth-session#' + digest(session_token), 'owner': claims['sub'],
            'session_id': digest(session_token), 'csrf': secrets.token_urlsafe(32), 'expires_at': int(time.time()) + 43200})
        return cookie(COOKIE, session_token, 43200)
