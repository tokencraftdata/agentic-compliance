"""The AWS stack (infra/cdk) synthesizes and keeps its security properties.
Skipped where aws-cdk-lib is not installed (pip install -r infra/cdk/requirements.txt)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra", "cdk"))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Match, Template
except ImportError:  # pragma: no cover
    cdk = None


@unittest.skipIf(cdk is None, "aws-cdk-lib not installed")
class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from finbot_stack import FinbotStack

        cls.t = Template.from_stack(FinbotStack(cdk.App(), "Finbot", env=cdk.Environment(region="us-east-1")))
        cls.json = json.dumps(cls.t.to_json())

    def test_tls_policy_and_http_redirects(self):
        self.t.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::Listener",
            {"Port": 443, "Protocol": "HTTPS", "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"},
        )
        self.t.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::Listener",
            {
                "Port": 80,
                "DefaultActions": [
                    Match.object_like({"Type": "redirect", "RedirectConfig": Match.object_like({"Protocol": "HTTPS"})})
                ],
            },
        )

    def test_alb_hardening(self):
        attrs = self.t.find_resources("AWS::ElasticLoadBalancingV2::LoadBalancer")
        (alb,) = attrs.values()
        kv = {a["Key"]: a["Value"] for a in alb["Properties"]["LoadBalancerAttributes"]}
        self.assertEqual(kv["routing.http.drop_invalid_header_fields.enabled"], "true")
        self.assertEqual(kv["routing.http.desync_mitigation_mode"], "strictest")
        self.assertEqual(kv["deletion_protection.enabled"], "true")
        self.assertEqual(kv["access_logs.s3.enabled"], "true")

    def test_waf_rules_attached(self):
        (acl,) = self.t.find_resources("AWS::WAFv2::WebACL").values()
        names = [r["Name"] for r in acl["Properties"]["Rules"]]
        for want in (
            "RatePerIp",
            "AWSManagedRulesCommonRuleSet",
            "AWSManagedRulesKnownBadInputsRuleSet",
            "AWSManagedRulesSQLiRuleSet",
            "AWSManagedRulesAmazonIpReputationList",
            "BodyTooLarge",
            "TokenInQueryString",
            "UnknownPath",
        ):
            self.assertIn(want, names)
        self.t.resource_count_is("AWS::WAFv2::WebACLAssociation", 1)
        self.t.has_resource_properties(
            "AWS::WAFv2::LoggingConfiguration", {"RedactedFields": [{"SingleHeader": {"Name": "authorization"}}]}
        )

    def test_keys_rotate_and_encrypt_everything(self):
        self.t.all_resources_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
        self.t.has_resource_properties(
            "AWS::DynamoDB::GlobalTable",
            {"SSESpecification": Match.object_like({"SSEEnabled": True, "SSEType": "KMS"})},
        )
        self.t.all_resources_properties("AWS::SQS::Queue", {"KmsMasterKeyId": Match.any_value()})
        self.assertIn("kms:EncryptionContext:purpose", self.json)  # pod roles: only with a finbot context

    def test_pod_roles_get_table_access(self):
        # Grants to imported roles must land in a policy; an immutable import drops them silently.
        self.assertIn("dynamodb:PutItem", self.json)
        self.assertIn("ApiRolePolicy", self.json)

    def test_session_items_need_the_tenant_role(self):
        # Pod roles: shared items only. Session items: the tenant role, confined by session tag.
        self.assertIn('"S#${aws:PrincipalTag/tenant_id}.*"', self.json)
        self.assertIn('"H#*"', self.json)
        self.assertNotIn('"dynamodb:Scan"', self.json)
        self.t.has_resource_properties("AWS::IAM::Role", {"RoleName": "finbot-tenant-data"})
        self.assertIn("sts:TagSession", self.json)
        self.assertIn("aws:RequestTag/tenant_id", self.json)

    def test_ticket_store_operations_are_allowed_without_widening_session_access(self):
        from finbot.security import Principal
        from finbot.store_dynamo import InMemoryDynamo
        from finbot.tenancy import TenantScopedTable, tenant_scope
        from finbot.tickets import DONE, DynamoTickets, TicketDesk, open_ticket

        policies = self.t.find_resources("AWS::IAM::Policy")
        for role in ("ApiRolePolicy", "WorkerRolePolicy"):
            (policy,) = [p["Properties"] for name, p in policies.items() if name.startswith(role)]
            statements = policy["PolicyDocument"]["Statement"]
            ticket_statements = [s for s in statements if s.get("Condition", {}).get(
                "ForAllValues:StringEquals", {}).get("dynamodb:LeadingKeys") == ["TICKETS"]]
            self.assertEqual(len(ticket_statements), 1)
            statement = ticket_statements[0]
            self.assertEqual(set(statement["Action"]), {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"})
            self.assertEqual(statement["Effect"], "Allow")
            self.assertNotEqual(statement["Resource"], "*")

            class PolicyTable(InMemoryDynamo):
                # Execute the real ticket adapter against the synthesized exact-partition grant.
                def __init__(self, grant):
                    super().__init__()
                    self.grant = grant

                def allowed(self, pk, action):
                    if pk not in self.grant["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"]:
                        raise PermissionError(pk)
                    if action not in self.grant["Action"]:
                        raise PermissionError(action)

                def get(self, pk, sk):
                    self.allowed(pk, "dynamodb:GetItem")
                    return super().get(pk, sk)

                def put(self, item, cond=None):
                    self.allowed(item["pk"], "dynamodb:PutItem")
                    return super().put(item, cond)

                def query(self, pk, sk_prefix):
                    self.allowed(pk, "dynamodb:Query")
                    return super().query(pk, sk_prefix)

            table = PolicyTable(statement)
            store = DynamoTickets(TenantScopedTable(table, lambda t: self.fail("tickets used tenant role")))
            with tenant_scope("m_100"):
                ticket, created = open_ticket(store, tenant_id="m_100", session_id="m_100.s1", role="customer",
                                              kind="refund", txn_id="txn_ABC123", text="refund please", facts={})
            self.assertTrue(created)
            desk = TicketDesk(store)
            support = Principal("staff", "internal", "support")
            self.assertEqual([t.id for t in desk.list(support)], [ticket.id])
            desk.claim(support, ticket.id)
            self.assertEqual(desk.resolve(support, ticket.id, DONE, "completed").status, DONE)
            with self.assertRaises(PermissionError):
                table.get("S#m_200.s1", "META")

    def test_tenant_role_is_exported_for_the_manifest(self):
        outputs = self.t.to_json()["Outputs"]
        self.assertIn("TenantDataRoleArn", outputs)
        self.assertIn("TenantDataRole", json.dumps(outputs["TenantDataRoleArn"]["Value"]))

    def test_audit_bucket_is_write_once(self):
        self.t.has_resource_properties(
            "AWS::S3::Bucket",
            {
                "ObjectLockEnabled": True,
                "ObjectLockConfiguration": {
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 365}},
                },
            },
        )
        self.t.all_resources_properties(
            "AWS::S3::Bucket",
            {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True,
                    "RestrictPublicBuckets": True,
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
