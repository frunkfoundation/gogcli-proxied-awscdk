"""Synthesized AWS contracts; replace only artifact bundling, never resources."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from aws_cdk import App, Environment, aws_lambda as lambda_
from aws_cdk.assertions import Match, Template

from stacks.google_proxy_api_substack import GoogleProxyApiStack


def unbundled_function(scope, identifier, *, entry, index, handler, bundling=None, **kwargs):
    return lambda_.Function(scope, identifier, code=lambda_.Code.from_inline('def handler(event, context): pass'),
                            handler=index.removesuffix('.py') + '.' + handler, **kwargs)


class InfrastructureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binding = json.loads((Path(__file__).resolve().parents[1] / 'env/gogcli-prd/binding.json').read_text())
        app = App()
        with patch('stacks.google_proxy_api_substack.PythonFunction', unbundled_function), \
                patch('stacks.onboarding_resources.PythonFunction', unbundled_function):
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

    def test_secret_deletion_is_limited_to_temporary_onboarding_parameters(self):
        deletion_resources = []
        for policy in self.template.find_resources('AWS::IAM::Policy').values():
            for statement in policy['Properties']['PolicyDocument']['Statement']:
                actions = statement['Action']
                if 'ssm:DeleteParameter' in ([actions] if isinstance(actions, str) else actions):
                    deletion_resources.extend(statement['Resource'])
        prefix = f"arn:aws:ssm:{self.binding['region']}:{self.binding['account_id']}:parameter{self.binding['ssm_prefix']}/onboarding/"
        self.assertEqual({prefix + name + '/*' for name in ('candidates', 'callbacks', 'attempts', 'owner-logins')}, set(deletion_resources))
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
        variables = onboarding['Environment']['Variables']
        self.assertEqual(self.binding['oidc_issuer'], variables['ONBOARDING_OIDC_ISSUER'])
        self.assertEqual(self.binding['oidc_client_id'], variables['ONBOARDING_OIDC_CLIENT_ID'])
        self.assertEqual(self.binding['owner_group'], variables['ONBOARDING_OWNER_GROUP'])
        self.assertEqual(self.binding['ssm_prefix'] + '/onboarding/oidc-client', variables['ONBOARDING_OIDC_SECRET_PARAMETER'])
        self.assertEqual(variables['GOG_PROXY_URL'], variables['PROXY_BASE_URL'])
        base_parts = variables['ONBOARDING_BASE_URL']['Fn::Join'][1]
        callback_parts = variables['GOOGLE_CALLBACK_URL']['Fn::Join'][1]
        self.assertEqual(base_parts[:-1], callback_parts[:-1])
        self.assertEqual(base_parts[-1] + 'oauth/google/callback', callback_parts[-1])
        self.assertTrue(base_parts[-1].endswith('/' + self.binding['stage'] + '/onboarding/'))
        stage_keys = set(self.template.find_resources('AWS::ApiGateway::Stage'))
        self.assertFalse(any(key in json.dumps(variables) for key in stage_keys))


if __name__ == '__main__':
    unittest.main()
