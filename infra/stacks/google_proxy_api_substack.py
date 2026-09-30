import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aws_cdk as cdk
from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from .python_asset import python_asset
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda" / "google_proxy"))
from onboarding_services import SCOPES_BY_SERVICE


@dataclass(frozen=True)
class GoogleProxyApiProps:
    stage_name: str = "prod"
    ssm_prefix: str = "/gogcli-proxy"
    lambda_timeout_seconds: int = 30
    lambda_memory_mb: int = 512
    role_prefix: str = "jdev-gogcli-proxy-prd"
    permissions_boundary_arn: str = ""
    ssm_key_arn: str = ""


class GoogleProxyApi(Construct):
    """
    "Sub-stack" construct for the gogcli -> API Gateway -> Lambda -> Google APIs proxy.
    Intended to be embedded into a larger CDK stack later.
    """

    def __init__(self, scope: Construct, construct_id: str, *, props: GoogleProxyApiProps) -> None:
        super().__init__(scope, construct_id)

        lambda_entry = Path(__file__).resolve().parents[1] / "lambda" / "google_proxy"
        if not lambda_entry.exists():
            raise ValueError(f"lambda entry not found: {lambda_entry}")

        boundary = iam.ManagedPolicy.from_managed_policy_arn(
            self, "RoleBoundary", props.permissions_boundary_arn
        )
        proxy_role = iam.Role(
            self, "ProxyRole", role_name=f"{props.role_prefix}-proxy",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            permissions_boundary=boundary,
        )
        proxy_logs = logs.LogGroup(
            self, "ProxyLogs", log_group_name=f"/aws/lambda/{props.role_prefix}-proxy",
            retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.RETAIN,
        )
        proxy_logs.grant_write(proxy_role)

        proxy_fn = lambda_.Function(
            self,
            "GoogleProxyHandler",
            code=python_asset(lambda_entry),
            handler="handler.handler",
            architecture=lambda_.Architecture.X86_64,
            runtime=lambda_.Runtime.PYTHON_3_11,
            function_name=f"{props.role_prefix}-proxy",
            role=proxy_role,
            log_group=proxy_logs,
            timeout=Duration.seconds(props.lambda_timeout_seconds),
            memory_size=props.lambda_memory_mb,
            environment={
                "GOG_SSM_PREFIX": props.ssm_prefix,
                "GOG_POLICY_ENABLED": os.getenv("GOG_POLICY_ENABLED", "true"),
                "GOG_POLICY_SSM_PREFIX": os.getenv(
                    "GOG_POLICY_SSM_PREFIX",
                    f"{props.ssm_prefix.rstrip('/')}/policy",
                ),
                "GOG_POLICY_CACHE_TTL_SECONDS": os.getenv("GOG_POLICY_CACHE_TTL_SECONDS", "30"),
                "GOG_POLICY_EVENTS_ENABLED": os.getenv("GOG_POLICY_EVENTS_ENABLED", "false"),
                "GOG_POLICY_EVENT_BUS_ARN": os.getenv("GOG_POLICY_EVENT_BUS_ARN", ""),
                # Defaults can be overridden per-account via SSM JSON.
                "GOG_DEFAULT_SCOPES": os.getenv(
                    "GOG_DEFAULT_SCOPES",
                    ",".join(SCOPES_BY_SERVICE.values()),
                ),
            },
        )

        proxy_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"], resources=[props.ssm_key_arn],
            conditions={"StringEquals": {"kms:ViaService": f"ssm.{Stack.of(self).region}.amazonaws.com"}},
        ))

        # SSM reads (allowlist + Google credentials). Narrow this down by prefix.
        prefix = props.ssm_prefix.rstrip("/")
        stack = Stack.of(self)
        proxy_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[
                    f"arn:aws:ssm:{stack.region}:{stack.account}:parameter{prefix}/allow/*",
                    f"arn:aws:ssm:{stack.region}:{stack.account}:parameter{prefix}/google/accounts/*",
                    f"arn:aws:ssm:{stack.region}:{stack.account}:parameter{prefix}/policy/*",
                ],
            )
        )

        event_bus_arn = (os.getenv("GOG_POLICY_EVENT_BUS_ARN") or "").strip()
        if event_bus_arn != "":
            proxy_fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["events:PutEvents"],
                    resources=[event_bus_arn],
                )
            )

        access_logs = logs.LogGroup(
            self,
            "AccessLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        api = apigw.RestApi(
            self,
            "GoogleProxyApi",
            rest_api_name="gogcli-google-proxy",
            cloud_watch_role=False,
            deploy_options=apigw.StageOptions(
                stage_name=props.stage_name,
                metrics_enabled=True,
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=False,
                access_log_destination=apigw.LogGroupLogDestination(access_logs),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=True,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True,
                ),
            ),
            # Allows Lambda to base64 responses for arbitrary Google content-types.
            binary_media_types=["*/*"],
            endpoint_types=[apigw.EndpointType.REGIONAL],
        )

        gateway_logs_role = iam.Role(
            self, "GatewayLogsRole", role_name=f"{props.role_prefix}-apigw-logs",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
            permissions_boundary=boundary,
        )
        # API Gateway validates its documented push-to-logs permissions at account setup.
        gateway_logs_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:DescribeLogGroups",
                     "logs:DescribeLogStreams", "logs:PutLogEvents", "logs:GetLogEvents", "logs:FilterLogEvents"],
            resources=["*"],
        ))
        gateway_account = apigw.CfnAccount(self, "GatewayAccount", cloud_watch_role_arn=gateway_logs_role.role_arn)
        gateway_account.node.add_dependency(gateway_logs_role, gateway_logs_role.node.find_child("DefaultPolicy"))
        api.deployment_stage.node.add_dependency(gateway_account)

        integration = apigw.LambdaIntegration(proxy_fn, proxy=True)

        # Root path (optional) and greedy proxy.
        api.root.add_method(
            "ANY",
            integration,
            authorization_type=apigw.AuthorizationType.IAM,
            api_key_required=True,
        )
        api.root.add_resource("{proxy+}").add_method(
            "ANY",
            integration,
            authorization_type=apigw.AuthorizationType.IAM,
            api_key_required=True,
        )

        api_key = apigw.ApiKey(self, "ClientApiKey", enabled=True)
        plan = api.add_usage_plan(
            "UsagePlan",
            name="gogcli-proxy",
            throttle=apigw.ThrottleSettings(rate_limit=50, burst_limit=100),
        )
        plan.add_api_key(api_key)
        plan.add_api_stage(api=api, stage=api.deployment_stage)

        self.api = api
        self.api_key = api_key
        self.lambda_fn = proxy_fn


class GoogleProxyApiStack(Stack):
    """
    Small wrapper stack so this construct can be deployed standalone.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage_name: str,
        ssm_prefix: str,
        binding: dict,
        env: Optional[cdk.Environment] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env, **kwargs)

        api = GoogleProxyApi(
            self,
            "Proxy",
            props=GoogleProxyApiProps(stage_name=stage_name, ssm_prefix=ssm_prefix,
                                      role_prefix=binding["role_prefix"],
                                      permissions_boundary_arn=binding["permissions_boundary_arn"],
                                      ssm_key_arn=binding["ssm_key_arn"]),
        )

        from .onboarding_resources import GoogleOnboarding
        GoogleOnboarding(self, "Onboarding", proxy=api, binding=binding)

        CfnOutput(self, "ProxyBaseUrl", value=api.api.url)
        CfnOutput(self, "ApiKeyId", value=api.api_key.key_id)
        CfnOutput(self, "ProxyInvokeArns", value=json.dumps([
            api.api.arn_for_execute_api(method, path, stage_name)
            for method, path in (("*", "/gmail/*"), ("*", "/drive/v3/*"),
                ("*", "/upload/drive/v3/*"), ("*", "/resumable/upload/drive/v3/*"),
                ("GET", "/proxy/v1/accounts"))
        ]))
