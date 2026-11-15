"""Security: who is calling, and what they may touch.

    Principal       identity from a VERIFIED token: subject, tenant, groups, role.
                    The request body never supplies tenant or role.
    TokenVerifier   strategy: HmacJwtVerifier (HS256 shared secret; dev/tests) or
                    JwksJwtVerifier (adapter for an OIDC provider such as Cognito,
                    Okta or Entra ID; needs PyJWT). Both check signature, exp, iss, aud.
    Authorizer      deny-by-default policy:
                      chat        role in {support, merchant}; session.tenant == principal.tenant
                      control     role == admin (drain/restore models, change routing)
                      review      role in {support, admin} (read the human review queue)

Every denial raises Forbidden; the service layer turns it into a 403 without
revealing whether the session exists (no tenant enumeration).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

CHAT_ROLES = {"support", "merchant"}


class AuthError(Exception):
    """Token missing, malformed, expired or not signed by a trusted issuer (401)."""


class Forbidden(Exception):
    """Authenticated, but not allowed to do this (403)."""


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    role: str
    groups: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_claims(cls, c: dict) -> Principal:
        try:
            return cls(str(c["sub"]), str(c["tenant_id"]), str(c["role"]), tuple(c.get("groups", ())))
        except KeyError as e:
            raise AuthError(f"token missing claim {e}") from None


# ------------------------------------------------------------------ token verification

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _check_registered(claims: dict, issuer: str, audience: str, now: float, leeway: float = 30) -> None:
    if "exp" not in claims or now > float(claims["exp"]) + leeway:
        raise AuthError("token expired")
    if "nbf" in claims and now + leeway < float(claims["nbf"]):
        raise AuthError("token not yet valid")
    if issuer and claims.get("iss") != issuer:
        raise AuthError("untrusted issuer")
    aud = claims.get("aud")
    if audience and audience not in (aud if isinstance(aud, list) else [aud]):
        raise AuthError("wrong audience")


class HmacJwtVerifier:
    """HS256 JWTs signed with a shared secret. For dev and tests; production should
    use asymmetric keys from the identity provider (JwksJwtVerifier)."""

    def __init__(self, secret: str, issuer: str = "", audience: str = "finbot",
                 clock: Callable[[], float] = time.time):
        if len(secret) < 32:
            raise ValueError("FINBOT_AUTH_SECRET must be at least 32 characters")
        self.key, self.issuer, self.audience, self.clock = secret.encode(), issuer, audience, clock

    def issue(self, claims: dict, ttl_s: int = 900) -> str:
        """Mint a token (tests / local CLI only)."""
        now = int(self.clock())
        body = {"iat": now, "exp": now + ttl_s, "aud": self.audience, **({"iss": self.issuer} if self.issuer else {}),
                **claims}
        head = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64e(json.dumps(body, separators=(",", ":")).encode())
        sig = _b64e(hmac.new(self.key, f"{head}.{payload}".encode(), hashlib.sha256).digest())
        return f"{head}.{payload}.{sig}"

    def verify(self, token: str) -> Principal:
        try:
            head, payload, sig = token.split(".")
            header = json.loads(_b64d(head))
            claims = json.loads(_b64d(payload))
        except Exception:  # noqa: BLE001
            raise AuthError("malformed token") from None
        if header.get("alg") != "HS256":  # blocks alg=none and algorithm confusion
            raise AuthError("unsupported algorithm")
        want = hmac.new(self.key, f"{head}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _b64d(sig)):
            raise AuthError("bad signature")
        _check_registered(claims, self.issuer, self.audience, self.clock())
        return Principal.from_claims(claims)


class JwksJwtVerifier:
    """Adapter for an OIDC identity provider (RS256/ES256 keys from its JWKS URL)."""

    def __init__(self, jwks_url: str, issuer: str, audience: str):
        import jwt  # lazy: pip install pyjwt[crypto]
        self._jwt, self._keys = jwt, jwt.PyJWKClient(jwks_url, cache_keys=True)
        self.issuer, self.audience = issuer, audience

    def verify(self, token: str) -> Principal:
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims = self._jwt.decode(token, key, algorithms=["RS256", "ES256"],
                                      audience=self.audience, issuer=self.issuer)
        except Exception as e:  # noqa: BLE001
            raise AuthError(f"invalid token: {type(e).__name__}") from None
        return Principal.from_claims(claims)


def make_verifier(settings, clock: Callable[[], float] = time.time):
    if settings.auth_mode == "jwks":
        return JwksJwtVerifier(settings.auth_jwks_url, settings.auth_issuer, settings.auth_audience)
    if settings.auth_mode == "hmac":
        return HmacJwtVerifier(settings.auth_secret, settings.auth_issuer, settings.auth_audience, clock)
    raise ValueError(f"unknown FINBOT_AUTH mode {settings.auth_mode!r}")


# ------------------------------------------------------------------ authorization

class Authorizer:
    def can_chat(self, p: Principal) -> None:
        if p.role not in CHAT_ROLES:
            raise Forbidden("role may not chat")

    def can_use_session(self, p: Principal, session_tenant: str) -> None:
        self.can_chat(p)
        if not hmac.compare_digest(p.tenant_id, session_tenant):
            raise Forbidden("not your session")

    def can_control(self, p: Principal) -> None:
        if p.role != "admin":
            raise Forbidden("control plane requires admin")

    def can_review(self, p: Principal) -> None:
        if p.role not in ("support", "admin"):
            raise Forbidden("review queue requires support or admin")
