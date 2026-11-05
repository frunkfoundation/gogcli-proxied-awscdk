#!/usr/bin/env python3
"""Import the canonical downloaded Web client and operator-confirmed API facts.

No Google APIs are enabled by this command. Confirm only services inspected in
Google Cloud Console. The downloaded JSON is read locally, never printed.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infra/lambda/google_proxy'))

from onboarding_google import validate_web_client
from onboarding_operations import OnboardingService
from onboarding_store import Store, WorkflowError

PROJECT_ID = 'gogcli-proxy'
SERVICES = ('gmail', 'drive', 'calendar')


def load_target(source, environment):
    """Read the same JSON environment binding/access records used by CDK."""
    source = Path(source).resolve(strict=True)
    if environment != 'gogcli-prd':
        raise ValueError('This operator command requires the gogcli-prd environment.')
    env_dir = source / 'env' / environment
    binding = json.loads((env_dir / 'binding.json').read_text())
    access = json.loads((env_dir / 'access.json').read_text())['GogcliGoogleProxyApi']
    application = binding['google_application']
    if (binding['environment'] != environment or application['project_id'] != PROJECT_ID
            or str(application['project_number']) != '1042630585914'
            or application['organization'] != 'kraecollective.com'):
        raise ValueError('The binding must identify the canonical gogcli-proxy application.')
    for key in ('deployment_profile', 'account_id', 'region', 'ssm_prefix', 'public_domain_name', 'stage'):
        if not isinstance(binding[key], str) or not binding[key].strip():
            raise ValueError('The environment binding is incomplete.')
    callback = f"https://{binding['public_domain_name']}/{binding['stage']}/onboarding/oauth/google/callback"
    if access['GoogleCallbackUrl'] != callback or not access['OnboardingTable']:
        raise ValueError('The access record must identify the exact bound callback and onboarding table.')
    return binding, access


def configure(source, environment, client_json, confirmed_apis, publishing_status, session_factory=None):
    binding, access = load_target(source, environment)
    if any(service not in SERVICES for service in confirmed_apis):
        raise ValueError('Confirm only Gmail, Drive, or Calendar APIs.')
    if publishing_status not in ('testing', 'production', 'unknown'):
        raise ValueError('Publishing status must be testing, production, or unknown.')
    document = json.loads(Path(client_json).read_text())
    client = validate_web_client(document, access['GoogleCallbackUrl'])
    if client['project_id'] != PROJECT_ID:
        raise ValueError('The downloaded Web client must belong to gogcli-proxy.')
    if session_factory is None:
        import boto3
        session_factory = boto3.Session
    session = session_factory(profile_name=binding['deployment_profile'], region_name=binding['region'])
    if session.client('sts').get_caller_identity()['Account'] != binding['account_id']:
        raise ValueError('The selected AWS profile does not match the environment account.')
    store = Store(session.resource('dynamodb').Table(access['OnboardingTable']),
                  session.client('ssm'), binding['ssm_prefix'])
    os.environ.update(GOOGLE_PROJECT_ID=PROJECT_ID,
                      GOOGLE_APPLICATION_NAME=binding['google_application']['name'],
                      GOOGLE_APPLICATION_ORGANIZATION=binding['google_application']['organization'])
    service = OnboardingService(store, None, access['GoogleCallbackUrl'])
    imported = service.import_client(document)
    reviewed = service.review_application({'clientRevision': imported['clientRevision'],
        'apiConfirmed': {name: name in confirmed_apis for name in SERVICES}, 'publishingStatus': publishing_status})
    # Output has an explicit allowlist: never serialize credentials or arbitrary AWS/provider exceptions.
    return {key: reviewed[key] for key in
            ('projectId', 'clientRevision', 'clientConfigured', 'apiConfirmed', 'publishingStatus') if key in reviewed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Explicit source repository root')
    parser.add_argument('--environment', required=True)
    parser.add_argument('--client-json', type=Path, required=True, help='Downloaded Web application JSON file')
    parser.add_argument('--confirmed-apis', required=True,
                        help='Comma-separated APIs actually inspected and enabled: gmail,drive,calendar; empty is allowed')
    parser.add_argument('--publishing-status', choices=('testing', 'production', 'unknown'), required=True)
    args = parser.parse_args(argv)
    try:
        result = configure(args.source, args.environment, args.client_json, [name.strip() for name in args.confirmed_apis.split(',') if name.strip()], args.publishing_status)
    except WorkflowError as exc:
        print(f'Application configuration failed ({exc.code}); no readiness result was returned.', file=sys.stderr)
        return 1
    except Exception:
        print('Application configuration failed; check the explicit target, local Web JSON, and AWS access. '
              'No readiness result was returned.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
