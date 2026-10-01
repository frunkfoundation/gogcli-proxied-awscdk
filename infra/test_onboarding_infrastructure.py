"""Synthesized AWS contracts; replace only artifact bundling, never resources."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from aws_cdk import App, Environment, aws_lambda as lambda_
from aws_cdk.assertions import Match, Template

from stacks.google_proxy_api_substack import GoogleProxyApiStack


def unbundled_asset(entry):
    return lambda_.Code.from_inline('def handler(event, context): pass')


class InfrastructureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binding = json.loads((Path(__file__).resolve().parents[1] / 'env/gogcli-prd/binding.json').read_text())
        cls.binding.update(public_domain_name='gp-1111111111111111111111.k-rae.com',
            public_hosted_zone_id='Z0123456789', public_hosted_zone_name='k-rae.com')
        app = App()
        with patch('stacks.google_proxy_api_substack.python_asset', unbundled_asset), \
                patch('stacks.onboarding_resources.python_asset', unbundled_asset):
            stack = GoogleProxyApiStack(app, 'Contract', stage_name=cls.binding['stage'],
                ssm_prefix=cls.binding['ssm_prefix'], binding=cls.binding,
                env=Environment(account=cls.binding['account_id'], region=cls.binding['region']))
        cls.template = Template.from_stack(stack)
        cls.resources = cls.template.to_json()['Resources']

    def test_owner_routes_are_separate_from_signed_proxy_routes(self):
        resources = self.template.find_resources('AWS::ApiGateway::Resource')
        onboarding = next(key for key, value in resources.items() if value['Properties']['PathPart'] == 'onboarding')
        owner_resources = {onboarding} | {key for key, value in resources.items()
            if value['Properties'].get('ParentId') == {'Ref': onboarding}}
        methods = self.template.find_resources('AWS::ApiGateway::Method')
        owner_methods = [value['Properties'] for value in methods.values()
            if value['Properties']['ResourceId'].get('Ref') in owner_resources]
        self.assertEqual(2, len(owner_methods))
        for method in owner_methods:
            self.assertEqual('NONE', method['AuthorizationType'])
            self.assertFalse(method['ApiKeyRequired'])
        signed_methods = [value['Properties'] for value in methods.values()
            if value['Properties']['ResourceId'].get('Ref') not in owner_resources]
        self.assertEqual(2, len(signed_methods))
        for method in signed_methods:
            self.assertEqual('AWS_IAM', method['AuthorizationType'])
            self.assertTrue(method['ApiKeyRequired'])

    def test_all_created_roles_have_explicit_names_and_shared_boundary(self):
        roles = self.template.find_resources('AWS::IAM::Role')
        self.assertEqual(3, len(roles))
        for value in roles.values():
            properties = value['Properties']
            self.assertTrue(properties['RoleName'].startswith(self.binding['role_prefix'] + '-'))
            self.assertEqual(self.binding['permissions_boundary_arn'], properties['PermissionsBoundary'])

    def test_download_storage_is_private_expiring_and_scoped_to_proxy(self):
        buckets = self.template.find_resources('AWS::S3::Bucket')
        self.assertEqual(1, len(buckets))
        bucket_id, bucket = next(iter(buckets.items()))
        self.assertEqual('Retain', bucket['DeletionPolicy'])
        self.assertEqual('Retain', bucket['UpdateReplacePolicy'])
        self.template.has_resource_properties('AWS::S3::Bucket', {
            'BucketEncryption': {'ServerSideEncryptionConfiguration': [
                {'ServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]},
            'PublicAccessBlockConfiguration': {'BlockPublicAcls': True, 'BlockPublicPolicy': True,
                'IgnorePublicAcls': True, 'RestrictPublicBuckets': True},
            'OwnershipControls': {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]},
            'LifecycleConfiguration': {'Rules': [Match.object_like({
                'Prefix': 'downloads/', 'Status': 'Enabled', 'ExpirationInDays': 1,
                'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}})]}})
        proxy_id, proxy = next((key, value['Properties'])
            for key, value in self.template.find_resources('AWS::Lambda::Function').items()
            if value['Properties']['Handler'] == 'handler.handler')
        self.assertEqual({'Ref': bucket_id}, proxy['Environment']['Variables']['GOG_DOWNLOAD_BUCKET'])
        role_id = proxy['Role']['Fn::GetAtt'][0]
        permissions = [(policy['Properties'], statement)
            for policy in self.template.find_resources('AWS::IAM::Policy').values()
            for statement in policy['Properties']['PolicyDocument']['Statement']
            if any(action.startswith('s3:') for action in
                ([statement['Action']] if isinstance(statement['Action'], str) else statement['Action']))]
        self.assertEqual(1, len(permissions))
        policy, statement = permissions[0]
        self.assertEqual([{'Ref': role_id}], policy['Roles'])
        self.assertEqual({'s3:GetObject', 's3:PutObject', 's3:DeleteObject'}, set(statement['Action']))
        download_arn = {'Fn::Join': ['', [{'Fn::GetAtt': [bucket_id, 'Arn']}, '/downloads/*']]}
        self.assertEqual(download_arn, statement['Resource'])
        policies = next(iter(self.template.find_resources('AWS::S3::BucketPolicy').values()))
        statements = policies['Properties']['PolicyDocument']['Statement']
        self.assertTrue(any(item['Effect'] == 'Deny' and item.get('Condition', {}).get('Bool') ==
            {'aws:SecureTransport': 'false'} for item in statements))
        self.assertTrue(any(item['Effect'] == 'Deny' and item['Action'] == 's3:GetObject'
            and item['Resource'] == download_arn and item.get('Condition', {}).get('NumericGreaterThan') ==
            {'s3:signatureAge': 300000} for item in statements))

    def test_secret_deletion_is_limited_to_account_lifecycle_and_temporary_parameters(self):
        deletion_resources = []
        for policy in self.template.find_resources('AWS::IAM::Policy').values():
            for statement in policy['Properties']['PolicyDocument']['Statement']:
                actions = statement['Action']
                if 'ssm:DeleteParameter' in ([actions] if isinstance(actions, str) else actions):
                    deletion_resources.extend(statement['Resource'])
        prefix = f"arn:aws:ssm:{self.binding['region']}:{self.binding['account_id']}:parameter{self.binding['ssm_prefix']}/onboarding/"
        self.assertEqual({prefix + name + '/*' for name in ('candidates', 'callbacks', 'attempts', 'owner-logins')} | {prefix.replace('/onboarding/', '/google/accounts/*'), prefix.replace('/onboarding/', '/policy/account/*')}, set(deletion_resources))
        for policy in self.template.find_resources('AWS::IAM::Policy').values():
            for statement in policy['Properties']['PolicyDocument']['Statement']:
                actions = statement['Action']
                if 'kms:Decrypt' in ([actions] if isinstance(actions, str) else actions):
                    self.assertEqual(self.binding['ssm_key_arn'], statement['Resource'])
                    self.assertEqual(f"ssm.{self.binding['region']}.amazonaws.com", statement['Condition']['StringEquals']['kms:ViaService'])

    def test_persisted_delivery_retries_and_encrypted_queues(self):
        self.template.has_resource_properties('AWS::DynamoDB::Table', {
            'StreamSpecification': {'StreamViewType': 'NEW_AND_OLD_IMAGES'},
            'TimeToLiveSpecification': {'AttributeName': 'expires_at', 'Enabled': True},
            'PointInTimeRecoverySpecification': {'PointInTimeRecoveryEnabled': True}})
        table = next(iter(self.template.find_resources('AWS::DynamoDB::Table').values()))
        self.assertEqual('Retain', table['DeletionPolicy'])
        queues = self.template.find_resources('AWS::SQS::Queue')
        self.assertEqual(2, len(queues))
        for value in queues.values():
            self.assertTrue(value['Properties']['SqsManagedSseEnabled'])
            self.assertEqual(240, value['Properties']['VisibilityTimeout'])
        self.template.has_resource_properties('AWS::SQS::Queue', {'RedrivePolicy': Match.object_like({'maxReceiveCount': 5})})
        self.template.resource_count_is('AWS::Lambda::EventSourceMapping', 3)
        self.template.has_resource_properties('AWS::Lambda::EventSourceMapping', {
            'StartingPosition': 'TRIM_HORIZON', 'MaximumRetryAttempts': -1,
            'BisectBatchOnFunctionError': True, 'FunctionResponseTypes': ['ReportBatchItemFailures']})

    def test_runtime_urls_and_auth_binding_match_their_consumers(self):
        functions = self.template.find_resources('AWS::Lambda::Function')
        onboarding = next(value['Properties'] for value in functions.values()
            if value['Properties']['Handler'] == 'onboarding.handler')
        self.assertEqual('python3.11', onboarding['Runtime'])
        self.assertEqual(['x86_64'], onboarding['Architectures'])
        variables = onboarding['Environment']['Variables']
        self.assertEqual(self.binding['oidc_issuer'], variables['ONBOARDING_OIDC_ISSUER'])
        self.assertEqual(self.binding['oidc_client_id'], variables['ONBOARDING_OIDC_CLIENT_ID'])
        self.assertEqual(self.binding['owner_group'], variables['ONBOARDING_OWNER_GROUP'])
        self.assertEqual(self.binding['ssm_prefix'] + '/onboarding/oidc-client', variables['ONBOARDING_OIDC_SECRET_PARAMETER'])
        self.assertEqual(variables['GOG_PROXY_URL'], variables['PROXY_BASE_URL'])
        setup_url = f"https://{self.binding['public_domain_name']}/{self.binding['stage']}/onboarding/"
        self.assertEqual(setup_url, variables['ONBOARDING_BASE_URL'])
        self.assertEqual(setup_url + 'oauth/google/callback', variables['GOOGLE_CALLBACK_URL'])
        stage_keys = set(self.template.find_resources('AWS::ApiGateway::Stage'))
        self.assertFalse(any(key in json.dumps(variables) for key in stage_keys))

    def test_public_callbacks_and_proxy_urls_survive_api_replacement(self):
        hostname = self.binding['public_domain_name']
        base_url = f"https://{hostname}/{self.binding['stage']}/"
        outputs = self.template.to_json()['Outputs']
        self.assertEqual(base_url, outputs['ProxyBaseUrl']['Value'])
        self.assertEqual(base_url + 'onboarding/oauth/google/callback', outputs['GoogleCallbackUrl']['Value'])
        self.assertEqual(base_url + 'onboarding/auth/callback', outputs['OwnerCallbackUrl']['Value'])
        self.template.has_resource_properties('AWS::ApiGateway::DomainName', {
            'DomainName': hostname, 'EndpointConfiguration': {'Types': ['REGIONAL']},
            'SecurityPolicy': 'TLS_1_2'})
        self.template.has_resource_properties('AWS::ApiGateway::BasePathMapping', {
            'BasePath': self.binding['stage'], 'Stage': {'Ref': next(iter(self.template.find_resources('AWS::ApiGateway::Stage')))}})
        self.template.has_resource_properties('AWS::CertificateManager::Certificate', {
            'DomainName': hostname, 'ValidationMethod': 'DNS',
            'DomainValidationOptions': [{'DomainName': hostname, 'HostedZoneId': self.binding['public_hosted_zone_id']}]})
        for resource_type in ('AWS::ApiGateway::DomainName', 'AWS::CertificateManager::Certificate', 'AWS::Route53::RecordSet'):
            resource = next(iter(self.template.find_resources(resource_type).values()))
            self.assertEqual('Retain', resource['DeletionPolicy'])
            self.assertEqual('Retain', resource['UpdateReplacePolicy'])
        self.template.has_resource_properties('AWS::Route53::RecordSet', {
            'Name': hostname + '.', 'Type': 'A', 'HostedZoneId': self.binding['public_hosted_zone_id'],
            'AliasTarget': Match.object_like({'DNSName': Match.any_value(), 'HostedZoneId': Match.any_value()})})

    def test_inventory_and_services_have_bounded_runtime_bindings(self):
        functions = self.template.find_resources('AWS::Lambda::Function')
        proxy = next(value['Properties'] for value in functions.values() if value['Properties']['Handler'] == 'handler.handler')
        onboarding = next(value['Properties'] for value in functions.values() if value['Properties']['Handler'] == 'onboarding.handler')
        self.assertEqual(self.binding['client_role_arn'], onboarding['Environment']['Variables']['ONBOARDING_OWNER_CALLER_ARN'])
        variables = onboarding['Environment']['Variables']
        application = self.binding['google_application']
        self.assertEqual(application['project_id'], variables['GOOGLE_PROJECT_ID'])
        self.assertEqual(application['name'], variables['GOOGLE_APPLICATION_NAME'])
        self.assertEqual(application['organization'], variables['GOOGLE_APPLICATION_ORGANIZATION'])
        self.assertNotIn('GOOGLE_EXISTING_PROJECTS', variables)
        self.assertNotIn('GOOGLE_PROJECT_NUMBER', variables)
        self.assertEqual(set(proxy['Environment']['Variables']['GOG_DEFAULT_SCOPES'].split(',')), {'https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar'})
        self.assertIn('ONBOARDING_TABLE', proxy['Environment']['Variables'])
        self.assertIn('ONBOARDING_SETUP_URL', proxy['Environment']['Variables'])
        outputs = self.template.to_json()['Outputs']
        self.assertNotIn('ProxyInvokeArn', outputs)
        arns = outputs['ProxyInvokeArns']['Value']['Fn::Join'][1]
        text = ''.join(part if isinstance(part, str) else '<api>' for part in arns)
        for suffix in ('prd/*/gmail/*', 'prd/*/drive/v3/*', 'prd/*/upload/drive/v3/*', 'prd/*/resumable/upload/drive/v3/*', 'prd/*/calendar/v3/*', 'prd/GET/proxy/v1/accounts'):
            self.assertIn(suffix, text)

    def test_account_ceiling_and_verifier_service_access_are_owned_and_narrow(self):
        statements = [statement for policy in self.template.find_resources('AWS::IAM::Policy').values()
            for statement in policy['Properties']['PolicyDocument']['Statement']]
        write_resources = [resource for statement in statements
            if 'ssm:PutParameter' in ([statement['Action']] if isinstance(statement['Action'], str) else statement['Action'])
            for resource in statement['Resource']]
        self.assertTrue(any('/policy/account/*' in resource for resource in write_resources))
        invoke_resources = [resource for statement in statements if statement['Action'] == 'execute-api:Invoke' for resource in statement['Resource']]
        rendered = json.dumps(invoke_resources)
        for endpoint in ('/gmail/v1/users/me/profile', '/gmail/v1/users/me/messages', '/drive/v3/about', '/drive/v3/files', '/calendar/v3/users/me/calendarList'):
            self.assertIn(endpoint, rendered)
        self.assertNotIn('/upload/', rendered)
        self.assertNotIn('/events', rendered)
        caller_policy = next(value['Properties'] for key, value in self.resources.items()
            if value['Type'] == 'AWS::SSM::Parameter' and 'CliPolicy' in key)
        self.assertEqual({'gmail', 'drive', 'calendar'},
            {rule['id'] for rule in json.loads(caller_policy['Value'])['rules']})


if __name__ == '__main__':
    unittest.main()
