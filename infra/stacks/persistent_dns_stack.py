import aws_cdk as cdk
from aws_cdk import Stack, CfnOutput, Fn
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_certificatemanager as acm
from constructs import Construct

class PersistentDnsStack(Stack):
    """Delegated public hosted zone for the app subdomain.
      Deploy-once; only AppStack is ever destroyed. Zone is RETAINed so the
      Spaceship NS delegation never breaks."""
      
    def __init__(self, scope: Construct, construct_id: str, *, subdomain: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hosted_zone = route53.PublicHostedZone(
            self, "HostedZone", zone_name=subdomain
        )
        self.hosted_zone.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
        
        self.certificate = acm.Certificate(
            self, "Certificate",
            domain_name=subdomain,
            validation=acm.CertificateValidation.from_dns(self.hosted_zone)
        )
        self.certificate.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
        
        CfnOutput(
            self, "NameServers",
            value=Fn.join(", ", self.hosted_zone.hosted_zone_name_servers),
            description="Add these as an NS record for 'phlaw' at Spaceship"
        )
        CfnOutput(self, "ZoneId", value=self.hosted_zone.hosted_zone_id)
        CfnOutput(self, "CertificateArn", value=self.certificate.certificate_arn)