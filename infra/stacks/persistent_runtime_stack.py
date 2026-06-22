from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class PersistentRuntimeStack(Stack):
    """Persistent runtime secrets(Qdrant + Anthropic API keys).

    Created with placeholder values; real values are set out-of-band via the
    AWS CLI so they never live in code or git. Deploy-once.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.qdrant_api_key = secretsmanager.Secret(
            self,
            "QdrantApiKey",
            secret_name="phlaw/qdrant-api-key",
            description="Qdrant Cloud API key for ph-law-rag",
        )

        self.anthropic_api_key = secretsmanager.Secret(
            self,
            "AnthropicApiKey",
            secret_name="phlaw/anthropic-api-key",
            description="Anthropic API key for ph-law-rag",
        )

        # Qdrant cluster endpoint. Not a credential, but kept out of git
        # (infra repo may be public) so the URL isn't exposed.
        self.qdrant_url = secretsmanager.Secret(
            self,
            "QdrantUrl",
            secret_name="phlaw/qdrant-url",
            description="Qdrant Cloud cluster URL for ph-law-rag",
        )

        CfnOutput(self, "QdrantApiKeyArn", value=self.qdrant_api_key.secret_arn)
        CfnOutput(self, "AnthropicApiKeyArn", value=self.anthropic_api_key.secret_arn)
        CfnOutput(self, "QdrantUrlArn", value=self.qdrant_url.secret_arn)
