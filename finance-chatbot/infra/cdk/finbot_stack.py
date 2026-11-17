"""finbot's AWS edge and data plane (AWS CDK, Python).

    internet -> WAF (managed + custom rules) -> ALB (TLS 1.2+, strong ciphers; HTTP->HTTPS)
             -> API pods (TargetGroupBinding)
    API / worker pods -> VPC endpoints -> DynamoDB, SQS, KMS, Bedrock     (no NAT for AWS traffic)

Keys:
    data key   KMS CMK, yearly automatic rotation. Encrypts the session table, the queues and,
               through finbot/crypto.py, every session state / user text / answer value inside
               them (envelope encryption with an encryption context per session and purpose).
               The key policy lets the pod roles use it only with a finbot `purpose` context.
    audit key  separate CMK for the audit bucket, so the people who can read sessions are not
               the people who can read (or erase) the audit trail.

Audit: S3 with Object Lock in COMPLIANCE mode for 365 days (PCI DSS 10.5.1 keeps a year of
logs); nobody, the root account included, can delete or overwrite a record before then.

The EKS cluster is out of scope here; pods join the target group through the AWS Load
Balancer Controller's TargetGroupBinding (deploy/k8s/targetgroupbinding.yaml), and pod
roles are passed in by ARN.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, CfnParameter, Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_wafv2 as waf
from constructs import Construct

PURPOSES = ["session_state", "user_text", "answer", "turn_meta"]  # finbot/crypto.py contexts
MAX_BODY = 16 * 1024  # finbot/http_api.py MAX_BODY
WAF_RATE_PER_5_MIN = 1000  # per client IP


def _managed(name: str, priority: int, count_rules: tuple[str, ...] = ()) -> waf.CfnWebACL.RuleProperty:
    return waf.CfnWebACL.RuleProperty(
        name=name,
        priority=priority,
        override_action=waf.CfnWebACL.OverrideActionProperty(none={}),
        statement=waf.CfnWebACL.StatementProperty(
            managed_rule_group_statement=waf.CfnWebACL.ManagedRuleGroupStatementProperty(
                vendor_name="AWS",
                name=name,
                rule_action_overrides=[
                    waf.CfnWebACL.RuleActionOverrideProperty(
                        name=r, action_to_use=waf.CfnWebACL.RuleActionProperty(count={})
                    )
                    for r in count_rules
                ]
                or None,
            )
        ),
        visibility_config=_vis(name),
    )


def _vis(name: str) -> waf.CfnWebACL.VisibilityConfigProperty:
    return waf.CfnWebACL.VisibilityConfigProperty(
        cloud_watch_metrics_enabled=True, metric_name=name, sampled_requests_enabled=True
    )


def _block(name: str, priority: int, statement: waf.CfnWebACL.StatementProperty) -> waf.CfnWebACL.RuleProperty:
    return waf.CfnWebACL.RuleProperty(
        name=name,
        priority=priority,
        statement=statement,
        action=waf.CfnWebACL.RuleActionProperty(block={}),
        visibility_config=_vis(name),
    )


def _lower(field: waf.CfnWebACL.FieldToMatchProperty, search: str, where: str) -> waf.CfnWebACL.StatementProperty:
    return waf.CfnWebACL.StatementProperty(
        byte_match_statement=waf.CfnWebACL.ByteMatchStatementProperty(
            field_to_match=field,
            positional_constraint=where,
            search_string=search,
            text_transformations=[waf.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")],
        )
    )


class FinbotStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw) -> None:
        super().__init__(scope, cid, **kw)
        cert_arn = CfnParameter(self, "CertificateArn", description="ACM certificate for the API hostname")
        api_role_arn = CfnParameter(self, "ApiRoleArn", description="IAM role of the API pods (IRSA)")
        worker_role_arn = CfnParameter(self, "WorkerRoleArn", description="IAM role of the worker pods (IRSA)")
        api_role = iam.Role.from_role_arn(self, "ApiRole", api_role_arn.value_as_string)
        worker_role = iam.Role.from_role_arn(self, "WorkerRole", worker_role_arn.value_as_string)

        # ------------------------------------------------------------ keys
        data_key = kms.Key(
            self,
            "DataKey",
            alias="alias/finbot-data",
            enable_key_rotation=True,
            rotation_period=Duration.days(365),
            removal_policy=RemovalPolicy.RETAIN,
            description="finbot sessions, journal and queues (envelope encryption)",
        )
        for role in (api_role, worker_role):
            data_key.add_to_resource_policy(
                iam.PolicyStatement(
                    principals=[iam.ArnPrincipal(role.role_arn)],
                    actions=["kms:GenerateDataKey", "kms:Decrypt"],
                    resources=["*"],
                    conditions={"ForAnyValue:StringEquals": {"kms:EncryptionContext:purpose": PURPOSES}},
                )
            )
        audit_key = kms.Key(
            self,
            "AuditKey",
            alias="alias/finbot-audit",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            description="finbot audit trail",
        )

        # ------------------------------------------------------------ data plane
        table = ddb.TableV2(
            self,
            "Sessions",
            table_name="finbot-sessions",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            time_to_live_attribute="expires",
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
            encryption=ddb.TableEncryptionV2.customer_managed_key(data_key),
        )
        dlq = sqs.Queue(
            self,
            "TurnsDlq",
            queue_name="finbot-turns-dlq.fifo",
            fifo=True,
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=data_key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "Turns",
            queue_name="finbot-turns.fifo",
            fifo=True,
            content_based_deduplication=False,
            visibility_timeout=Duration.seconds(150),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=data_key,
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=5),
        )
        # Tenant isolation in IAM (finbot/tenancy.py). Session items are keyed "S#<tenant>.<uuid>".
        # The pod roles may touch only shared items (breaker health, control, rate counters);
        # session items need the tenant data role, assumed per request with the session tag
        # tenant_id taken from the verified caller, and confined to that tenant's keys.
        ddb_actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                       "dynamodb:Query", "dynamodb:ConditionCheckItem"]
        via_dynamodb = {"StringEquals": {"kms:ViaService": f"dynamodb.{self.region}.amazonaws.com"}}
        kms_for_table = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*", "kms:DescribeKey"]
        tenant_role = iam.Role(
            self,
            "TenantDataRole",
            role_name="finbot-tenant-data",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.CompositePrincipal(
                iam.ArnPrincipal(api_role.role_arn), iam.ArnPrincipal(worker_role.role_arn)
            ).with_session_tags(),
            description="finbot session data for one tenant at a time (session tag tenant_id)",
        )
        tenant_role.add_to_policy(
            iam.PolicyStatement(
                actions=ddb_actions + ["dynamodb:TransactWriteItems"],
                resources=[table.table_arn],
                conditions={
                    "ForAllValues:StringLike": {
                        "dynamodb:LeadingKeys": ["S#${aws:PrincipalTag/tenant_id}.*", "OP#*"]
                    }
                },
            )
        )
        tenant_role.add_to_policy(
            iam.PolicyStatement(actions=kms_for_table, resources=[data_key.key_arn], conditions=via_dynamodb)
        )
        for role in (api_role, worker_role):
            role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=ddb_actions,
                    resources=[table.table_arn],
                    conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["H#*", "C#*", "R#*"]}},
                )
            )
            role.add_to_principal_policy(
                iam.PolicyStatement(actions=kms_for_table, resources=[data_key.key_arn], conditions=via_dynamodb)
            )
            role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole", "sts:TagSession"],
                    resources=[tenant_role.role_arn],
                    conditions={"StringLike": {"aws:RequestTag/tenant_id": "?*"}},
                )
            )
        # Tickets are a shared support-desk resource, separate from tenant-isolated sessions.
        # Only the operations used by DynamoTickets, on its exact partition; never table-wide access.
        for role in (api_role, worker_role):
            role.add_to_principal_policy(iam.PolicyStatement(
                actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"],
                resources=[table.table_arn],
                conditions={"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["TICKETS"]}},
            ))
        queue.grant_send_messages(api_role)
        queue.grant_consume_messages(worker_role)
        dlq.grant_send_messages(worker_role)

        audit = s3.Bucket(
            self,
            "Audit",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=audit_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.compliance(Duration.days(365)),
            removal_policy=RemovalPolicy.RETAIN,
        )
        audit.grant_put(worker_role)
        audit.grant_put(api_role)
        alb_logs = s3.Bucket(
            self,
            "AlbLogs",
            encryption=s3.BucketEncryption.S3_MANAGED,  # ALB logs need SSE-S3
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(400))],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ------------------------------------------------------------ network
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=3,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="app", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=20),
            ],
        )
        vpc.add_flow_log("FlowLogs")
        vpc.add_gateway_endpoint("DynamoDb", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)
        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)
        for name, svc in (
            ("Kms", ec2.InterfaceVpcEndpointAwsService.KMS),
            ("Sqs", ec2.InterfaceVpcEndpointAwsService.SQS),
            ("Sts", ec2.InterfaceVpcEndpointAwsService.STS),
            ("Logs", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS),
            ("Bedrock", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
        ):
            vpc.add_interface_endpoint(name, service=svc, private_dns_enabled=True)

        # ------------------------------------------------------------ ALB
        alb_sg = ec2.SecurityGroup(
            self, "AlbSg", vpc=vpc, allow_all_outbound=False, description="finbot ALB: HTTPS in, pods out"
        )
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "redirected to HTTPS")
        pod_sg = ec2.SecurityGroup(self, "ApiPodSg", vpc=vpc, description="finbot API pods: only from the ALB")
        pod_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8080), "ALB to pods")
        alb_sg.add_egress_rule(pod_sg, ec2.Port.tcp(8080), "to pods")

        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            deletion_protection=True,
            drop_invalid_header_fields=True,
            desync_mitigation_mode=elbv2.DesyncMitigationMode.STRICTEST,
            idle_timeout=Duration.seconds(120),
        )
        alb.log_access_logs(alb_logs, prefix="alb")
        targets = elbv2.ApplicationTargetGroup(
            self,
            "ApiTargets",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(path="/healthz", healthy_http_codes="200", interval=Duration.seconds(10)),
        )
        alb.add_listener(
            "Https",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            ssl_policy=elbv2.SslPolicy.TLS13_RES,  # TLS 1.3, and 1.2 with forward-secret AEAD ciphers only
            certificates=[acm.Certificate.from_certificate_arn(self, "Cert", cert_arn.value_as_string)],
            default_target_groups=[targets],
            open=False,
        )
        alb.add_redirect(source_port=80, target_port=443)

        # ------------------------------------------------------------ WAF
        uri, query = (
            waf.CfnWebACL.FieldToMatchProperty(uri_path={}),
            waf.CfnWebACL.FieldToMatchProperty(query_string={}),
        )
        rules = [
            waf.CfnWebACL.RuleProperty(
                name="RatePerIp",
                priority=0,
                action=waf.CfnWebACL.RuleActionProperty(block={}),
                statement=waf.CfnWebACL.StatementProperty(
                    rate_based_statement=waf.CfnWebACL.RateBasedStatementProperty(
                        aggregate_key_type="IP", limit=WAF_RATE_PER_5_MIN
                    )
                ),
                visibility_config=_vis("RatePerIp"),
            ),
            _managed("AWSManagedRulesAmazonIpReputationList", 1),
            _managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
            # the API allows 16 KB bodies; the common set's 8 KB body rule is replaced by BodyTooLarge
            _managed("AWSManagedRulesCommonRuleSet", 3, count_rules=("SizeRestrictions_BODY",)),
            _managed("AWSManagedRulesSQLiRuleSet", 4),
            _managed("AWSManagedRulesAnonymousIpList", 5, count_rules=("HostingProviderIPList",)),
            _block(
                "BodyTooLarge",
                6,
                waf.CfnWebACL.StatementProperty(
                    size_constraint_statement=waf.CfnWebACL.SizeConstraintStatementProperty(
                        comparison_operator="GT",
                        size=MAX_BODY,
                        field_to_match=waf.CfnWebACL.FieldToMatchProperty(
                            body=waf.CfnWebACL.BodyProperty(oversize_handling="MATCH")
                        ),
                        text_transformations=[waf.CfnWebACL.TextTransformationProperty(priority=0, type="NONE")],
                    )
                ),
            ),
            # credentials belong in the Authorization header; one in a URL ends up in logs
            _block(
                "TokenInQueryString",
                7,
                waf.CfnWebACL.StatementProperty(
                    or_statement=waf.CfnWebACL.OrStatementProperty(
                        statements=[_lower(query, "token=", "CONTAINS"), _lower(query, "authorization=", "CONTAINS")]
                    )
                ),
            ),
            # only the API's own paths reach the pods
            _block(
                "UnknownPath",
                8,
                waf.CfnWebACL.StatementProperty(
                    not_statement=waf.CfnWebACL.NotStatementProperty(
                        statement=waf.CfnWebACL.StatementProperty(
                            or_statement=waf.CfnWebACL.OrStatementProperty(
                                statements=[_lower(uri, "/v1/", "STARTS_WITH"), _lower(uri, "/healthz", "EXACTLY")]
                            )
                        )
                    )
                ),
            ),
        ]
        acl = waf.CfnWebACL(
            self,
            "WebAcl",
            scope="REGIONAL",
            default_action=waf.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=_vis("finbot"),
            rules=rules,
        )
        waf.CfnWebACLAssociation(self, "WebAclAlb", resource_arn=alb.load_balancer_arn, web_acl_arn=acl.attr_arn)
        waf_logs = logs.LogGroup(
            self,
            "WafLogs",
            log_group_name="aws-waf-logs-finbot",
            retention=logs.RetentionDays.ONE_YEAR,
            encryption_key=audit_key,
        )
        audit_key.grant_encrypt_decrypt(iam.ServicePrincipal(f"logs.{self.region}.amazonaws.com"))
        waf.CfnLoggingConfiguration(
            self,
            "WafLogging",
            resource_arn=acl.attr_arn,
            log_destination_configs=[waf_logs.log_group_arn],
            redacted_fields=[waf.CfnLoggingConfiguration.FieldToMatchProperty(single_header={"Name": "authorization"})],
        )

        for name, value in (
            ("AlbDns", alb.load_balancer_dns_name),
            ("TargetGroupArn", targets.target_group_arn),
            ("ApiPodSecurityGroup", pod_sg.security_group_id),
            ("DataKeyArn", data_key.key_arn),
            ("WebAclArn", acl.attr_arn),
            ("QueueUrl", queue.queue_url),
            ("AuditBucket", audit.bucket_name),
            ("SessionsTable", table.table_name),
            ("TenantDataRoleArn", tenant_role.role_arn),
        ):
            CfnOutput(self, name, value=value)
