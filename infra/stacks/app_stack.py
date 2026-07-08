import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk import aws_servicediscovery as servicediscovery
from constructs import Construct

from stacks.persistent_dns_stack import PersistentDnsStack
from stacks.persistent_runtime_stack import PersistentRuntimeStack

# Non-secret runtime config (safe to commit). Secrets come from Secrets Manager.
ECR_REPO = "ph-law-rag"
IMAGE_TAG = "latest"
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"

API_ENVIRONMENT = {
    "embedding_backend": "bedrock",
    "aws_region": "us-east-1",
    "qdrant_collection": "ph_law-titan1024",
    "llm_model": "claude-haiku-4-5-20251001",
    # Qwen3 is the eval-quality local default, but it is not viable on the
    # small CPU-only Fargate task. Pin the deployed demo to the serving backend.
    "reranker_backend": "minilm",
    "enable_query_rewriting": "false",
    "answerability_gate_enabled": "false",
    "faithfulness_selfcheck_enabled": "false",
    "query_decomposition_enabled": "false",
    "trace_logging_enabled": "true",
    "router_enabled": "true",
}


class AppStack(Stack):
    """Destroyable app runtime: network + ECS cluster + arm64 task defs.
    Public subnets, NO NAT (cost). Access controlled by 3 SGs, not topology.
    Services + ALB + Service Connect are added in later steps."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime: PersistentRuntimeStack,
        dns: PersistentDnsStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---------- network ----------
        # Public subnets only, no NAT gateway. 2 AZs (ALB needs >=2).
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # --- 3 security groups (the real access control) ---
        self.alb_sg = ec2.SecurityGroup(
            self,
            "AlbSg",
            vpc=self.vpc,
            description="ALB: public HTTPS/HTTP in",
            allow_all_outbound=True,
        )
        self.alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS from internet"
        )
        self.alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP (redirect to 443)"
        )

        self.ui_sg = ec2.SecurityGroup(
            self,
            "UiSg",
            vpc=self.vpc,
            description="UI task: only from ALB",
            allow_all_outbound=True,
        )
        self.ui_sg.add_ingress_rule(
            self.alb_sg, ec2.Port.tcp(8501), "Streamlit from ALB only"
        )

        self.api_sg = ec2.SecurityGroup(
            self,
            "ApiSg",
            vpc=self.vpc,
            description="API task: only from UI, NO public ingress",
            allow_all_outbound=True,
        )
        self.api_sg.add_ingress_rule(
            self.ui_sg, ec2.Port.tcp(8000), "FastAPI from UI only"
        )

        # ---------- ECS cluster + Service Connect namespace ----------
        self.cluster = ecs.Cluster(self, "Cluster", vpc=self.vpc)
        # Explicit HTTP namespace, referenced by ARN in the services below so
        # CloudFormation orders it BEFORE them (avoids "Failed to retrieve
        # namespace" race). Name "local" -> API advertises as api.local.
        self.namespace = servicediscovery.HttpNamespace(
            self,
            "ServiceConnectNs",
            name="local",
        )

        # shared image (one image, two entrypoints)
        repo = ecr.Repository.from_repository_name(self, "EcrRepo", ECR_REPO)
        image = ecs.ContainerImage.from_ecr_repository(repo, IMAGE_TAG)

        arm64 = ecs.RuntimePlatform(
            cpu_architecture=ecs.CpuArchitecture.ARM64,
            operating_system_family=ecs.OperatingSystemFamily.LINUX,
        )

        # ---------- API task def ----------
        # Task role: Bedrock InvokeModel on Titan v2 (replaces local AWS creds).
        api_task_role = iam.Role(
            self,
            "ApiTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        api_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/{TITAN_MODEL_ID}"
                ],
            )
        )

        self.api_log = logs.LogGroup(
            self,
            "ApiLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        self.api_task = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDef",
            cpu=512,
            memory_limit_mib=2048,
            runtime_platform=arm64,
            task_role=api_task_role,
        )
        api_container = self.api_task.add_container(
            "api",
            image=image,
            command=[
                "uvicorn",
                "app.api.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ],
            logging=ecs.LogDriver.aws_logs(stream_prefix="api", log_group=self.api_log),
            environment=API_ENVIRONMENT,
            secrets={
                # CDK grants the execution role read access to these automatically.
                "qdrant_api_key": ecs.Secret.from_secrets_manager(
                    runtime.qdrant_api_key
                ),
                "anthropic_api_key": ecs.Secret.from_secrets_manager(
                    runtime.anthropic_api_key
                ),
                "qdrant_url": ecs.Secret.from_secrets_manager(runtime.qdrant_url),
            },
            health_check=ecs.HealthCheck(
                # image has no curl; use the baked-in venv python
                command=[
                    "CMD-SHELL",
                    'python -c "import urllib.request; '
                    "urllib.request.urlopen('http://localhost:8000/health')\" "
                    "|| exit 1",
                ],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(60),
            ),
        )
        # named port -> required by Service Connect (Step 6)
        api_container.add_port_mappings(
            ecs.PortMapping(name="api", container_port=8000)
        )

        # ---------- UI task def ----------
        self.ui_log = logs.LogGroup(
            self,
            "UiLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        self.ui_task = ecs.FargateTaskDefinition(
            self,
            "UiTaskDef",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=arm64,
        )
        ui_container = self.ui_task.add_container(
            "ui",
            image=image,
            command=[
                "streamlit",
                "run",
                "app/ui/home.py",
                "--server.address",
                "0.0.0.0",
                "--server.port",
                "8501",
            ],
            logging=ecs.LogDriver.aws_logs(stream_prefix="ui", log_group=self.ui_log),
            environment={
                # Service Connect resolves the short dns_name "api" (not api.local)
                "api_base_url": "http://api:8000",
                "llm_model": "claude-haiku-4-5-20251001",
            },
        )
        ui_container.add_port_mappings(ecs.PortMapping(name="ui", container_port=8501))

        # ---------- API service (internal-only; advertises api.local:8000) ----------
        self.api_service = ecs.FargateService(
            self,
            "ApiService",
            cluster=self.cluster,
            task_definition=self.api_task,
            desired_count=1,
            security_groups=[self.api_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,  # egress via IGW (no NAT); SG blocks public inbound
            min_healthy_percent=100,
            max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            service_connect_configuration=ecs.ServiceConnectProps(
                namespace=self.namespace.namespace_arn,
                services=[
                    ecs.ServiceConnectService(
                        port_mapping_name="api",
                        dns_name="api",
                        port=8000,
                    )
                ],
            ),
        )

        # ---------- UI service (client of api.local; ALB target added later) ----------
        self.ui_service = ecs.FargateService(
            self,
            "UiService",
            cluster=self.cluster,
            task_definition=self.ui_task,
            desired_count=1,
            security_groups=[self.ui_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
            min_healthy_percent=100,
            max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            service_connect_configuration=ecs.ServiceConnectProps(
                namespace=self.namespace.namespace_arn,
            ),  # client only
        )

        # ---------- ALB (fronts UI only; API is internal via Service Connect) -----
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=self.vpc,
            internet_facing=True,
            security_group=self.alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # HTTPS:443 with the persistent ACM cert -> UI target group
        https = self.alb.add_listener(
            "Https",
            port=443,
            certificates=[
                elbv2.ListenerCertificate.from_arn(dns.certificate.certificate_arn)
            ],
            # alb_sg already allows 443/80; do not add duplicate 0.0.0.0/0 rules.
            open=False,
        )
        https.add_targets(
            "UiTarget",
            port=8501,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[
                self.ui_service.load_balancer_target(
                    container_name="ui",
                    container_port=8501,
                )
            ],
            health_check=elbv2.HealthCheck(
                path="/_stcore/health",
                healthy_http_codes="200",
            ),
        )

        # HTTP:80 -> permanent redirect to HTTPS:443
        self.alb.add_listener(
            "Http",
            port=80,
            open=False,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS",
                port="443",
                permanent=True,
            ),
        )

        # ---------- Route 53 alias (apex of the delegated child zone) -> ALB ----------
        alias_target = route53.RecordTarget.from_alias(
            route53_targets.LoadBalancerTarget(self.alb)
        )
        route53.ARecord(self, "AliasA", zone=dns.hosted_zone, target=alias_target)
        route53.AaaaRecord(self, "AliasAAAA", zone=dns.hosted_zone, target=alias_target)

        cdk.CfnOutput(self, "AlbDnsName", value=self.alb.load_balancer_dns_name)
        cdk.CfnOutput(self, "AppUrl", value=f"https://{dns.hosted_zone.zone_name}")
