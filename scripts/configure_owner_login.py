#!/usr/bin/env python3
"""Configure this app on existing PRD Authentik; credentials never leave secret stores.
Run with uv run --with requests --with boto3 python scripts/configure_owner_login.py.
"""
import json
from pathlib import Path
import subprocess

import boto3
import requests

ROOT = Path(__file__).resolve().parents[1]
binding = json.loads((ROOT / 'env/gogcli-prd/binding.json').read_text())
access = json.loads((ROOT / 'env/gogcli-prd/access.json').read_text())['GogcliGoogleProxyApi']
base = 'https://auth.k-rae.com/api/v3/'
slug = 'gogcli-proxy-prd'
credential_slug = 'project-gogcli-proxy-prd-owner-oidc'
session = requests.Session()
session.headers['Authorization'] = 'Bearer ' + subprocess.check_output(
    ['keyring', 'credential', 'get-field', 'project-krae-authentik-prd-api', 'token'], text=True).strip()


def call(method, endpoint, body=None):
    response = session.request(method, base + endpoint, json=body, timeout=30)
    if not response.ok:
        raise RuntimeError(f'Authentik {method} {endpoint.split("?")[0]} returned {response.status_code}')
    return response.json() if response.content else None


def objects(endpoint):
    result = call('GET', endpoint + '?page_size=100')
    if result['pagination']['next']:
        raise RuntimeError(f'Paginated discovery must be handled before mutating {endpoint}')
    return result['results']


def exact(items, field, value):
    found = [item for item in items if item.get(field) == value]
    if len(found) != 1:
        raise RuntimeError(f'Expected one {field}={value}, found {len(found)}')
    return found[0]


schema = call('GET', 'schema/?format=json')
for required in ('OAuth2ProviderRequest', 'ApplicationRequest', 'GroupRequest', 'PolicyBindingRequest'):
    if required not in schema['components']['schemas']:
        raise RuntimeError('Unsupported Authentik API schema')
user = exact(objects('core/users/'), 'username', 'jay.jakosky')
groups = objects('core/groups/')
group = next((item for item in groups if item['name'] == binding['owner_group']), None)
if not group:
    group = call('POST', 'core/groups/', {'name': binding['owner_group'], 'users': [user['pk']], 'is_superuser': False})
else:
    group = call('PATCH', 'core/groups/' + group['pk'] + '/', {'users': [user['pk']], 'is_superuser': False})
flows = objects('flows/instances/')
mappings = objects('propertymappings/provider/scope/')
key = exact(objects('crypto/certificatekeypairs/'), 'name', 'authentik Self-signed Certificate')
provider_body = {'name': 'Gogcli Proxy PRD Owner', 'client_id': binding['oidc_client_id'],
    'client_type': 'confidential', 'grant_types': ['authorization_code'], 'issuer_mode': 'per_provider',
    'sub_mode': 'user_uuid', 'include_claims_in_id_token': True, 'signing_key': key['pk'],
    'authorization_flow': exact(flows, 'slug', 'default-provider-authorization-implicit-consent')['pk'],
    'invalidation_flow': exact(flows, 'slug', 'default-provider-invalidation-flow')['pk'],
    'redirect_uris': [{'matching_mode': 'strict', 'url': access['OwnerCallbackUrl'], 'redirect_uri_type': 'authorization'}],
    'property_mappings': [exact(mappings, 'managed', 'goauthentik.io/providers/oauth2/scope-' + scope)['pk'] for scope in ('openid', 'email', 'profile')]}
providers = objects('providers/oauth2/')
provider = next((item for item in providers if item.get('client_id') == binding['oidc_client_id']), None)
provider = call('PATCH', f"providers/oauth2/{provider['pk']}/", provider_body) if provider else call('POST', 'providers/oauth2/', provider_body)
client_secret = provider['client_secret']
if not client_secret:
    raise RuntimeError('Provider did not supply a confidential client secret')
# Capture new client credentials immediately in the developer catalog, independently of runtime SSM.
metadata = subprocess.run(['keyring', 'credential', 'show', credential_slug], capture_output=True, text=True)
if metadata.returncode:
    subprocess.run(['keyring', 'credential', 'create', '--slug', credential_slug, '--provider', 'authentik',
        '--display-name', 'Gogcli Proxy PRD Owner Login', '--kind', 'oauth_client', '--owner', 'K-Rae', '--purpose', 'Gogcli Proxy PRD owner login',
        '--documentation', binding['oidc_issuer'], '--metadata-only'], check=True, capture_output=True)
for field, value in [('client_id', binding['oidc_client_id']), ('client_secret', client_secret)]:
    subprocess.run(['keyring', 'credential', 'set-field', credential_slug, field, '--value-stdin'], input=value,
        text=True, check=True, capture_output=True)
ssm = boto3.Session(profile_name=binding['deployment_profile'], region_name=binding['region']).client('ssm')
ssm.put_parameter(Name=binding['ssm_prefix'] + '/onboarding/oidc-client', Type='SecureString', KeyId='alias/aws/ssm',
    Value=json.dumps({'client_id': binding['oidc_client_id'], 'client_secret': client_secret}), Overwrite=True)
apps = objects('core/applications/')
app = next((item for item in apps if item['slug'] == slug), None)
app_body = {'name': 'Gmail Account Connections', 'slug': slug, 'provider': provider['pk'],
    'meta_launch_url': access['SetupUrl'], 'policy_engine_mode': 'all'}
app = call('PATCH', f'core/applications/{slug}/', app_body) if app else call('POST', 'core/applications/', app_body)
policy_bindings = objects('policies/bindings/')
found = [item for item in policy_bindings if item.get('target') == app['pk'] and item.get('group') == group['pk']]
if not found:
    call('POST', 'policies/bindings/', {'target': app['pk'], 'group': group['pk'], 'order': 0, 'enabled': True, 'failure_result': False})
readback = call('GET', f"providers/oauth2/{provider['pk']}/")
assert readback['client_id'] == binding['oidc_client_id']
assert readback['redirect_uris'] == provider_body['redirect_uris']
assert call('GET', f"core/groups/{group['pk']}/")['users'] == [user['pk']]
response = requests.get(binding['oidc_issuer'] + '.well-known/openid-configuration', timeout=15)
response.raise_for_status()
assert response.json()['issuer'] == binding['oidc_issuer']
jwks = requests.get(response.json()['jwks_uri'], timeout=15)
jwks.raise_for_status()
assert any(key.get('kty') == 'RSA' for key in jwks.json()['keys'])
record = {'service': 'https://auth.k-rae.com/', 'stage': 'prd', 'application': slug,
    'provider_id': provider['pk'], 'owner_group': group['pk'], 'owner_username': user['username'],
    'client_id': binding['oidc_client_id'], 'client_type': 'confidential',
    'scopes': ['openid', 'profile', 'email'], 'issuer': binding['oidc_issuer'],
    'callback': access['OwnerCallbackUrl'], 'secret_catalog': credential_slug,
    'runtime_secret_parameter': binding['ssm_prefix'] + '/onboarding/oidc-client',
    'verification': 'discovery, JWKS, exact redirect and owner group passed',
    'interactive_verification': 'Owner sign-in and issued claims require the owner browser session'}
(ROOT / 'env/gogcli-prd/owner-login.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
