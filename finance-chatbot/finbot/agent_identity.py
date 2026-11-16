"""Agent identity: what the bot may do on a user's behalf, checked at every tool call.

The model never holds a credential. For each turn, code mints a short-lived delegation
grant (an OAuth token-exchange style token, RFC 8693):

    sub        the user the turn is for
    act.sub    the agent acting for them ("finbot-agent"), so logs show both
    tenant_id  from the user's verified token
    sid        the session; a grant from one session is refused in another
    scope      the user's scopes, cut to what the agent is allowed (never wider than either)
    exp        a few minutes: a leaked grant is soon useless

Every tool call then needs (1) the scope the tool declares in TOOL_SCOPES or its domain
plug-in, and (2) a grant that verifies for this tenant, session and scope. A tool with no
declared scope does not run (deny by default). A user token with scope "accounts:read"
lets the bot read accounts and nothing else, however the model is prompted.

Remote tool services (MCP servers, internal APIs) verify the same grant with
`GrantIssuer.verify`, so the check holds at the data owner too, not only in the bot.
Production: sign with an asymmetric KMS key (kms:Sign) and publish the public key, so
tool services verify without holding a secret. HS256 here keeps the code dependency-free.
"""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .security import AuthError, hs256_decode, hs256_sign

AGENT = "finbot-agent"
AUDIENCE = "finbot-tools"
ALL_READ = "read:*"

# Scope each non-domain tool needs. Domain plug-in tools need "<domain>:read".
TOOL_SCOPES: dict[str, str] = {
    "search_knowledge_base": "kb:read",
    "get_transaction": "payments:read",
    "search_transaction_logs": "payments:read",
    "get_routing_trace": "payments:read",
    "get_processor_health": "payments:read",
}

# A role's scopes when the token carries none. The agent may only ever read.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "support": frozenset({ALL_READ}),
    "customer": frozenset({ALL_READ}),
    "merchant": frozenset({ALL_READ}),
}
AGENT_CEILING = frozenset({ALL_READ})   # nothing the agent does may write, whoever asks


class GrantDenied(Exception):
    pass


def scope_for_tool(tool: str) -> str | None:
    from .domains import by_tool
    if tool in TOOL_SCOPES:
        return TOOL_SCOPES[tool]
    d = by_tool(tool)
    return f"{d.name}:read" if d is not None else None


def allows(scopes: Iterable[str], needed: str) -> bool:
    """`needed` is "<resource>:<action>"; "read:*" grants every read."""
    have = set(scopes)
    _, action = needed.split(":", 1)
    return needed in have or f"{action}:*" in have


def effective_scopes(role: str, token_scopes: Iterable[str] | None) -> frozenset[str]:
    """The token's scopes (or the role's defaults), cut to what the role allows."""
    role_max = ROLE_SCOPES.get(role, frozenset())
    asked = frozenset(token_scopes) if token_scopes else role_max
    return frozenset(s for s in asked if allows(role_max, s))


def delegate(user_scopes: Iterable[str]) -> frozenset[str]:
    """Scopes the agent gets: the user's, never above the agent's ceiling."""
    return frozenset(s for s in user_scopes if s in AGENT_CEILING or allows(AGENT_CEILING, s))


@dataclass(frozen=True)
class Grant:
    grant_id: str
    subject: str
    actor: str
    tenant_id: str
    session_id: str
    scopes: frozenset[str]
    expires_at: float


class GrantIssuer:
    def __init__(self, key: bytes | None = None, ttl_s: int = 300, clock: Callable[[], float] = time.time,
                 kid: str = "g1"):
        # No key configured: a random per-process key. Grants are then checked in-process only,
        # which is all a single worker needs; remote tool services need a shared or KMS key.
        self.key = key if key else os.urandom(32)
        if len(self.key) < 32:
            raise ValueError("FINBOT_GRANT_KEY must be at least 32 bytes")
        self.ttl_s, self.clock, self.kid = ttl_s, clock, kid

    def mint(self, subject: str, tenant_id: str, session_id: str, scopes: Iterable[str]) -> str:
        now = int(self.clock())
        claims = {"sub": subject or "anonymous", "act": {"sub": AGENT}, "tenant_id": tenant_id, "sid": session_id,
                  "scope": " ".join(sorted(delegate(scopes))), "aud": AUDIENCE, "iat": now,
                  "exp": now + self.ttl_s, "jti": uuid.uuid4().hex}
        return hs256_sign(self.key, self.kid, claims)

    def verify(self, token: str, *, tool: str, tenant_id: str, session_id: str) -> Grant:
        """The grant, if it lets this tool run for this tenant and session; else GrantDenied."""
        try:
            c = hs256_decode(token, {self.kid: self.key}, self.kid)
        except AuthError as e:
            raise GrantDenied(str(e)) from None
        if c.get("aud") != AUDIENCE or float(c.get("exp", 0)) < self.clock():
            raise GrantDenied("grant expired or not for tools")
        if c.get("tenant_id") != tenant_id or c.get("sid") != session_id:
            raise GrantDenied("grant is for another tenant or session")
        needed = scope_for_tool(tool)
        scopes = frozenset(str(c.get("scope", "")).split())
        if needed is None or not allows(scopes, needed):
            raise GrantDenied(f"grant lacks scope {needed or 'for an undeclared tool'}")
        return Grant(c["jti"], c["sub"], c["act"]["sub"], c["tenant_id"], c["sid"], scopes, float(c["exp"]))
