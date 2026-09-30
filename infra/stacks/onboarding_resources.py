"""Owner-only remote enrollment beside the IAM-authenticated Google proxy."""
import base64
import json
import sys
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from .python_asset import python_asset
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda" / "google_proxy"))
from onboarding_services import SCOPES_BY_SERVICE, probes, service_policy, verifier_policy


def encoded(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


class GoogleOnboarding(Construct):
    def __init__(self, scope, construct_id, *, proxy, binding):
        super().__init__(scope, construct_id)
        stack = Stack.of(self)
        prefix, role_prefix = binding["ssm_prefix"], binding["role_prefix"]
        table = dynamodb.Table(self, "State", partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST, stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="expires_at", point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            removal_policy=RemovalPolicy.RETAIN)
        dlq = sqs.Queue(self, "DeadLetters", encryption=sqs.QueueEncryption.SQS_MANAGED, retention_period=Duration.days(14), visibility_timeout=Duration.seconds(240))
        queue = sqs.Queue(self, "Verification", encryption=sqs.QueueEncryption.SQS_MANAGED, visibility_timeout=Duration.seconds(240),
            retention_period=Duration.days(14), dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=5))
        role = iam.Role(self, "Role", role_name=f"{role_prefix}-onboarding",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            permissions_boundary=iam.ManagedPolicy.from_managed_policy_arn(self, "Boundary", binding["permissions_boundary_arn"]))
        log_group = logs.LogGroup(self, "Logs", log_group_name=f"/aws/lambda/{role_prefix}-onboarding",
            retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.RETAIN)
        log_group.grant_write(role)
        proxy_url = proxy.public_base_url
        setup_url = proxy_url + "onboarding/"
        google_callback = setup_url + "oauth/google/callback"
        owner_callback = setup_url + "auth/callback"
        secret_parameter = prefix + "/onboarding/oidc-client"
        function = lambda_.Function(self, "Handler",
            code=python_asset(Path(__file__).resolve().parents[1] / "lambda" / "google_proxy"),
            handler="onboarding.handler", runtime=lambda_.Runtime.PYTHON_3_11, architecture=lambda_.Architecture.X86_64,
            function_name=f"{role_prefix}-onboarding", role=role, log_group=log_group,
            timeout=Duration.seconds(40), memory_size=512,
            environment={"ONBOARDING_TABLE": table.table_name, "GOG_SSM_PREFIX": prefix,
                "GOOGLE_PROJECT_ID": binding["google_application"]["project_id"],
                "GOOGLE_APPLICATION_NAME": binding["google_application"]["name"],
                "GOOGLE_APPLICATION_ORGANIZATION": binding["google_application"]["organization"],
                "ONBOARDING_OWNER_CALLER_ARN": binding["client_role_arn"],
                "ONBOARDING_BASE_URL": setup_url, "GOOGLE_CALLBACK_URL": google_callback,
                "GOG_PROXY_URL": proxy_url, "PROXY_BASE_URL": proxy_url,
                "ONBOARDING_OIDC_ISSUER": binding["oidc_issuer"],
                "ONBOARDING_OIDC_CLIENT_ID": binding["oidc_client_id"],
                "ONBOARDING_OIDC_SECRET_PARAMETER": secret_parameter,
                "ONBOARDING_OWNER_GROUP": binding["owner_group"],
                "ONBOARDING_QUEUE_URL": queue.queue_url, "ONBOARDING_DLQ_ARN": dlq.queue_arn,
                "PROXY_API_KEY_ID": proxy.api_key.key_id, "ONBOARDING_VERIFIER_ROLE_ARN": role.role_arn})
        table.grant_read_write_data(role)
        proxy.lambda_fn.add_environment("ONBOARDING_TABLE", table.table_name)
        proxy.lambda_fn.add_environment("ONBOARDING_SETUP_URL", setup_url)
        proxy.lambda_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Scan"], resources=[table.table_arn]))
        function.add_event_source(sources.DynamoEventSource(table, starting_position=lambda_.StartingPosition.TRIM_HORIZON,
            batch_size=10, retry_attempts=-1, bisect_batch_on_error=True, report_batch_item_failures=True))
        function.add_event_source(sources.SqsEventSource(queue, batch_size=1, report_batch_item_failures=True))
        function.add_event_source(sources.SqsEventSource(dlq, batch_size=1, report_batch_item_failures=True))
        queue.grant_send_messages(role)
        parameter_arn = f"arn:aws:ssm:{stack.region}:{stack.account}:parameter"
        allow_paths = [prefix + "/allow/" + encoded(binding["client_role_arn"]), prefix + "/allow/" + encoded(f"arn:aws:iam::{binding['account_id']}:role/{role_prefix}-onboarding")]
        role.add_to_policy(iam.PolicyStatement(actions=["ssm:GetParameter", "ssm:PutParameter"],
            resources=[parameter_arn + p for p in [prefix + "/google/accounts/*", prefix + "/policy/account/*", prefix + "/onboarding/*", *allow_paths]]))
        role.add_to_policy(iam.PolicyStatement(actions=["ssm:DeleteParameter"], resources=[parameter_arn + prefix + "/onboarding/" + p + "/*" for p in ("candidates", "callbacks", "attempts", "owner-logins")] + [parameter_arn + prefix + suffix for suffix in ("/google/accounts/*", "/policy/account/*")]))
        role.add_to_policy(iam.PolicyStatement(actions=["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], resources=[binding["ssm_key_arn"]],
            conditions={"StringEquals": {"kms:ViaService": f"ssm.{stack.region}.amazonaws.com"}}))
        role.add_to_policy(iam.PolicyStatement(actions=["apigateway:GET"], resources=[f"arn:aws:apigateway:{stack.region}::/apikeys/{proxy.api_key.key_id}"]))
        role.add_to_policy(iam.PolicyStatement(actions=["execute-api:Invoke"], resources=[proxy.api.arn_for_execute_api("GET", path, binding["stage"]) for service in SCOPES_BY_SERVICE for _, path, _ in probes(service)]))
        integration = apigw.LambdaIntegration(function)
        resource = proxy.api.root.add_resource("onboarding")
        resource.add_method("ANY", integration, authorization_type=apigw.AuthorizationType.NONE, api_key_required=False)
        resource.add_resource("{proxy+}").add_method("ANY", integration, authorization_type=apigw.AuthorizationType.NONE, api_key_required=False)
        ssm.StringParameter(self, "DefaultPolicy", parameter_name=prefix + "/policy/default",
            string_value=json.dumps({"version": 1, "default_effect": "deny", "rules": []}))
        for label, caller, policy in [
            ("Cli", binding["client_role_arn"], service_policy(list(SCOPES_BY_SERVICE))),
            ("Verifier", f"arn:aws:iam::{binding['account_id']}:role/{role_prefix}-onboarding", verifier_policy())]:
            ssm.StringParameter(self, label + "Policy", parameter_name=prefix + "/policy/caller/" + encoded(caller),
                string_value=json.dumps(policy))
        cloudwatch.Alarm(self, "StreamDispatchAge", metric=function.metric("IteratorAge"), threshold=3600000, evaluation_periods=1)
        cloudwatch.Alarm(self, "WorkerErrors", metric=function.metric_errors(), threshold=1, evaluation_periods=1)
        cloudwatch.Alarm(self, "DeadLetterBacklog", metric=dlq.metric_approximate_number_of_messages_visible(), threshold=1, evaluation_periods=1)
        for name, value in [("SetupUrl", setup_url), ("GoogleCallbackUrl", google_callback), ("OwnerCallbackUrl", owner_callback), ("OnboardingTable", table.table_name), ("OnboardingRoleArn", role.role_arn)]:
            CfnOutput(stack, name, value=value)
