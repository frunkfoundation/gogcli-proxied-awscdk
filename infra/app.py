#!/usr/bin/env python3

import json
from pathlib import Path

import aws_cdk as cdk

from stacks.google_proxy_api_substack import GoogleProxyApiStack


app = cdk.App()
environment = app.node.try_get_context("environment") or "gogcli-prd"
binding_file = Path(__file__).resolve().parents[1] / "env" / environment / "binding.json"
binding = json.loads(binding_file.read_text())

GoogleProxyApiStack(
    app,
    "GogcliGoogleProxyApi",
    stage_name=binding["stage"],
    ssm_prefix=binding["ssm_prefix"],
    binding=binding,
    env=cdk.Environment(account=binding["account_id"], region=binding["region"]),
    synthesizer=cdk.CliCredentialsStackSynthesizer(),
)

app.synth()
