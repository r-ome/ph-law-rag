#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stacks.persistent_dns_stack import PersistentDnsStack
from stacks.persistent_runtime_stack import PersistentRuntimeStack
from stacks.app_stack import AppStack

app = cdk.App()

account = os.environ.get("CDK_DEFAULT_ACCOUNT")
region = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
subdomain = os.environ.get("PHLAW_SUBDOMAIN", "phlaw.jeromeagapay.com")

env = cdk.Environment(account=account, region=region)

dns = PersistentDnsStack(app, "PhlawPersistentDnsStack", subdomain=subdomain, env=env)
runtime = PersistentRuntimeStack(app, "PhlawPersistentRuntimeStack", env=env)
AppStack(app, "PhlawAppStack", runtime=runtime, dns=dns, env=env)

app.synth()
