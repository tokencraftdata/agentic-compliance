"""CDK entry point: cd infra/cdk && cdk deploy -c region=us-east-1 --parameters CertificateArn=... ..."""
import aws_cdk as cdk
from finbot_stack import FinbotStack

app = cdk.App()
FinbotStack(app, "Finbot", env=cdk.Environment(region=app.node.try_get_context("region") or "us-east-1"))
app.synth()
