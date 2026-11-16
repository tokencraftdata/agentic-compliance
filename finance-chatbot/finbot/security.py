"""Security: who is calling, and what they may touch.

    Principal       identity from a VERIFIED token: subject, tenant, groups, role, scopes.
                    The request body never supplies tenant, role or scopes.
    TokenVerifier   strategy: HmacJwtVerifier (HS256 shared secret; dev/tests) or
                    JwksJwtVerifier (adapter for an OIDC provider such as Cognito,
                    Okta or Entra ID; needs PyJWT). Both check signature, exp, iss, aud,
                    and the revocation list (token id `jti`, or every token of a subject).
    Key rotation    HS256 keys are a key ring addressed by the header's `kid`: new tokens
                    are signed with the current key, tokens signed with a previous key still
                    verify until that key is removed. JWKS rotates at the identity provider.
    Revocations     store-backed deny list shared by every API pod (control record
                    "revocations"), cached for a few seconds.
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

CHAT_ROLES = {"support", "customer", "merchant"}  # "merchant" is a legacy alias of "customer"


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
    # OAuth scopes from the token's "scope" claim. Empty: the role's defaults
    # (finbot/agent_identity.py ROLE_SCOPES). A token can narrow a role, never widen it.
    scopes: tuple[str, ...] = field(default_factory=tuple)
    token_id: str = ""      # jti, for revocation

    @classmethod
    def from_claims(cls, c: dict) -> Principal:
        try:
            scope = c.get("scope", "")
            scopes = tuple(scope.split() if isinstance(scope, str) else scope)
            return cls(str(c["sub"]), str(c["tenant_id"]), str(c["role"]), tuple(c.get("groups", ())),
                       scopes, str(c.get("jti", "")))
        except KeyError as e:
            raise AuthError(f"token missing claim {e}") from None


# ------------------------------------------------------------------ revocation

class Revocations:
    """Deny list kept in the session store's control table, so every pod sees a revocation
    within `cache_s`. Revoke one token by its jti (until it would have expired anyway), or
    every token of a subject issued before now (account compromise, offboarding)."""

    KEY = "revocations"

    def __init__(self, store, clock: Callable[[], float] = time.time, cache_s: float = 2.0):
        self.store, self.clock, self.cache_s = store, clock, cache_s
        self._cached: tuple[float, dict] | None = None

    def _load(self, fresh: bool = False) -> dict:
        now = self.clock()
        if fresh or self._cached is None or now - self._cached[0] > self.cache_s:
            self._cached = (now, self.store.read_control(self.KEY) or {"jti": {}, "subjects": {}})
        return self._cached[1]

    def _save(self, doc: dict) -> None:
        now = self.clock()
        doc["jti"] = {j: exp for j, exp in doc["jti"].items() if exp > now}   # expired tokens fail anyway
        self.store.write_control(self.KEY, doc)
        self._cached = (now, doc)

    def revoke_token(self, jti: str, expires_at: float) -> None:
        doc = self._load(fresh=True)
        doc["jti"][jti] = expires_at
        self._save(doc)

    def revoke_subject(self, subject: str) -> None:
        doc = self._load(fresh=True)
        doc["subjects"][subject] = self.clock()
        self._save(doc)

    def check(self, claims: dict) -> None:
        doc = self._load()
        if claims.get("jti") and claims["jti"] in doc["jti"]:
            raise AuthError("token revoked")
        before = doc["subjects"].get(str(claims.get("sub")))
        if before is not None and float(claims.get("iat", 0)) <= before:
            raise AuthError("token revoked")


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


def hs256_sign(key: bytes, kid: str, claims: dict) -> str:
    head = _b64e(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    payload = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(key, f"{head}.{payload}".encode(), hashlib.sha256).digest())
    return f"{head}.{payload}.{sig}"


def hs256_decode(token: str, keys: dict[str, bytes], default_kid: str) -> dict:
    """Claims of an HS256 token signed by a key in `keys` (chosen by the header's kid).
    Checks the signature only; the caller checks exp, iss, aud and its own claims."""
    try:
        head, payload, sig = token.split(".")
        header = json.loads(_b64d(head))
        claims = json.loads(_b64d(payload))
    except Exception:  # noqa: BLE001
        raise AuthError("malformed token") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise AuthError("malformed token")
    if header.get("alg") != "HS256":  # blocks alg=none and algorithm confusion
        raise AuthError("unsupported algorithm")
    key = keys.get(str(header.get("kid", default_kid)))
    if key is None:
        raise AuthError("unknown signing key")
    want = hmac.new(key, f"{head}.{payload}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(want, _b64d(sig)):
        raise AuthError("bad signature")
    return claims


def parse_key_ring(spec: str) -> dict[str, str]:
    """"kid:secret,kid:secret" -> {kid: secret} (previous signing keys still accepted)."""
    pairs = [p.split(":", 1) for p in spec.split(",") if p.strip()]
    if any(len(p) != 2 for p in pairs):
        raise ValueError("expected kid:secret[,kid:secret...]")
    return {k.strip(): v for k, v in pairs}


class HmacJwtVerifier:
    """HS256 JWTs signed with a shared secret. For dev and tests; production should
    use asymmetric keys from the identity provider (JwksJwtVerifier)."""

    def __init__(self, secret: str, issuer: str = "", audience: str = "finbot",
                 clock: Callable[[], float] = time.time, kid: str = "k1",
                 previous: dict[str, str] | None = None, revocations: Revocations | None = None):
        for s in (secret, *(previous or {}).values()):
            if len(s) < 32:
                raise ValueError("FINBOT_AUTH_SECRET must be at least 32 characters")
        self.kid, self.issuer, self.audience, self.clock = kid, issuer, audience, clock
        self.keys = {**{k: v.encode() for k, v in (previous or {}).items()}, kid: secret.encode()}
        self.revocations = revocations

    def issue(self, claims: dict, ttl_s: int = 900) -> str:
        """Mint a token (tests / local CLI only)."""
        import uuid
        now = int(self.clock())
        body = {"iat": now, "exp": now + ttl_s, "aud": self.audience, "jti": uuid.uuid4().hex,
                **({"iss": self.issuer} if self.issuer else {}), **claims}
        return hs256_sign(self.keys[self.kid], self.kid, body)

    def verify(self, token: str) -> Principal:
        claims = hs256_decode(token, self.keys, self.kid)
        _check_registered(claims, self.issuer, self.audience, self.clock())
        if self.revocations is not None:
            self.revocations.check(claims)
        return Principal.from_claims(claims)


class JwksJwtVerifier:
    """Adapter for an OIDC identity provider (RS256/ES256 keys from its JWKS URL)."""

    def __init__(self, jwks_url: str, issuer: str, audience: str, revocations: Revocations | None = None):
        import jwt  # lazy: pip install pyjwt[crypto]
        self._jwt, self._keys = jwt, jwt.PyJWKClient(jwks_url, cache_keys=True)
        self.issuer, self.audience, self.revocations = issuer, audience, revocations

    def verify(self, token: str) -> Principal:
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims = self._jwt.decode(token, key, algorithms=["RS256", "ES256"],
                                      audience=self.audience, issuer=self.issuer)
        except Exception as e:  # noqa: BLE001
            raise AuthError(f"invalid token: {type(e).__name__}") from None
        if self.revocations is not None:
            self.revocations.check(claims)
        return Principal.from_claims(claims)


def make_verifier(settings, clock: Callable[[], float] = time.time, revocations: Revocations | None = None):
    if settings.auth_mode == "jwks":
        return JwksJwtVerifier(settings.auth_jwks_url, settings.auth_issuer, settings.auth_audience, revocations)
    if settings.auth_mode == "hmac":
        return HmacJwtVerifier(settings.auth_secret, settings.auth_issuer, settings.auth_audience, clock,
                               kid=settings.auth_kid, previous=parse_key_ring(settings.auth_previous_secrets),
                               revocations=revocations)
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

    def can_revoke(self, p: Principal) -> None:
        if p.role != "admin":
            raise Forbidden("revoking tokens requires admin")

    def can_review(self, p: Principal) -> None:
        if p.role not in ("support", "admin"):
            raise Forbidden("review queue requires support or admin")
