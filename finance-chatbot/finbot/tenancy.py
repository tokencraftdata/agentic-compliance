"""The tenant a request runs for, and data access confined to it.

`tenant_scope(tenant)` marks the code that runs for one verified tenant: the API sets it from
the caller's token, a queue worker from the message's tenant (already checked against the
session). Everything below reads `current_tenant()`.

`TenantScopedTable` is a DynamoDB table port that sends every call on a session item
(`S#<tenant>.<uuid>`) through credentials for that tenant only. The credentials come from
assuming the tenant data role with the session tag `tenant_id=<current tenant>`; the role's
policy allows only items whose partition key starts with `S#${aws:PrincipalTag/tenant_id}.`
(the `dynamodb:LeadingKeys` condition in infra/cdk). So reading another tenant's session fails
twice: here, because the key names a different tenant than the request, and in IAM, even if
this check had a bug. Shared items (breaker health `H#`, control `C#`, rate counters `R#`) use
the pod's own role, which in turn cannot read session items.

The tag comes from the request, never from the key being read: a tag taken from the key would
only compare a session with itself.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from collections.abc import Callable, Iterator

from .crypto import session_tenant

_TENANT: contextvars.ContextVar[str | None] = contextvars.ContextVar("finbot_tenant", default=None)


class TenantViolation(PermissionError):
    """Data access outside the request's tenant, or with no tenant at all."""


def current_tenant() -> str | None:
    return _TENANT.get()


@contextlib.contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    if not tenant_id:
        raise TenantViolation("empty tenant")
    token = _TENANT.set(tenant_id)
    try:
        yield
    finally:
        _TENANT.reset(token)


class TenantScopedTable:
    """DynamoTable port: session items go through `for_tenant(current tenant)`, everything else
    through `base`."""

    def __init__(self, base, for_tenant: Callable[[str], object]):
        self.base, self.for_tenant = base, for_tenant

    def _pick(self, pk: str):
        scoped = current_tenant()
        if pk.startswith("OP#") and scoped is not None:
            # An external action's pointer is written in one transaction with the session's
            # action record, so it must use the same (tenant) credentials. It holds only a
            # session id; the tenant role may write OP# items (infra/cdk).
            return self.for_tenant(scoped)
        if not pk.startswith("S#"):
            return self.base
        tenant = current_tenant()
        if tenant is None:
            raise TenantViolation("session data accessed outside a tenant scope")
        named = session_tenant(pk[2:])
        if named is not None and named != tenant:
            raise TenantViolation("session belongs to another tenant")
        return self.for_tenant(tenant)

    def get(self, pk, sk):
        return self._pick(pk).get(pk, sk)

    def put(self, item, cond=None):
        return self._pick(item["pk"]).put(item, cond)

    def query(self, pk, sk_prefix):
        return self._pick(pk).query(pk, sk_prefix)

    def remove_attributes(self, pk, sk, attributes, cond):
        return self._pick(pk).remove_attributes(pk, sk, attributes, cond)

    def add(self, pk, sk, n, expires):
        return self._pick(pk).add(pk, sk, n, expires)

    def transact_put(self, writes):
        tables = {id(self._pick(i["pk"])): self._pick(i["pk"]) for i, _ in writes}
        if len(tables) != 1:
            raise TenantViolation("one transaction may not span tenants")
        return next(iter(tables.values())).transact_put(writes)


class AssumedRoleTables:
    """for_tenant factory: a Boto3Dynamo per tenant, on credentials from
    sts:AssumeRole(role, Tags=[tenant_id]) that are refreshed before they expire."""

    def __init__(self, table: str, region: str, role_arn: str, duration_s: int = 900, sts=None,
                 make_table: Callable | None = None, clock: Callable[[], float] = time.time):
        self.table, self.region, self.role_arn, self.duration_s = table, region, role_arn, duration_s
        self.clock = clock
        if sts is None:
            import boto3
            sts = boto3.client("sts", region_name=region)
        self.sts = sts
        self.make_table = make_table or self._boto_table
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _boto_table(self, creds: dict):
        import boto3

        from .store_dynamo import Boto3Dynamo
        client = boto3.client("dynamodb", region_name=self.region, aws_access_key_id=creds["AccessKeyId"],
                              aws_secret_access_key=creds["SecretAccessKey"], aws_session_token=creds["SessionToken"])
        return Boto3Dynamo(self.table, self.region, client=client)

    def __call__(self, tenant: str):
        with self._lock:
            hit = self._cache.get(tenant)
            if hit and hit[0] > self.clock() + 60:             # refresh a minute before expiry
                return hit[1]
            r = self.sts.assume_role(RoleArn=self.role_arn, RoleSessionName=f"finbot-{tenant}"[:64],
                                     DurationSeconds=self.duration_s, Tags=[{"Key": "tenant_id", "Value": tenant}])
            t = self.make_table(r["Credentials"])
            self._cache[tenant] = (self.clock() + self.duration_s, t)
            return t
