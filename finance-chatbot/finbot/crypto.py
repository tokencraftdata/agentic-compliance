"""Encryption at rest: envelope encryption with AES-256-GCM, keys held by a key provider.

    seal(obj, context)  -> "fbenc1:<kid>.<wrapped key>.<nonce>.<ciphertext>"   a fresh 256-bit data key per value,
                                                    wrapped by the provider's key-encryption key
    open(blob, context) -> obj                      fails if the blob, the wrapped key or the
                                                    context was changed

The context (for example {"session_id": "...", "purpose": "session_state"}) is bound to the
ciphertext as AES-GCM associated data and, with KMS, as the encryption context. A value
copied into another session's row therefore does not decrypt, and every KMS Decrypt call is
logged in CloudTrail with the session it was for.

Key providers:
    LocalKeyProvider   key ring {kid: 32-byte key} from FINBOT_LOCAL_KEYS. Dev and tests only.
    KmsKeyProvider     AWS KMS customer-managed key: GenerateDataKey / Decrypt. The key never
                       leaves KMS; rotation is KMS automatic rotation, which keeps old key
                       material, so existing values still decrypt.

Rotation: seal always uses the provider's active key; open uses the key named in the blob.
`rewrap` re-seals a value under the active key, for retiring an old local key.

Values that are not sealed (no "fbenc1:" prefix) are returned as parsed JSON, so turning
encryption on does not break sessions written before it; `require` refuses them instead.

Tenant binding. Session ids now start with the tenant ("m_100.<uuid>"), and `ctx` adds the
tenant to the context. Such values are sealed as "fbenc2:"; a value copied into another
tenant's row does not decrypt, and CloudTrail shows whose data each KMS Decrypt was for.
"fbenc1:" values, written before tenant binding, still open with the context they were sealed
under (session and purpose). The prefix is chosen when sealing, so it cannot be downgraded.
"""
from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from typing import Any, Protocol

PREFIX = "fbenc1:"       # context: session, purpose
PREFIX2 = "fbenc2:"      # context: tenant, session, purpose
_PREFIXES = (PREFIX2, PREFIX)


def _sealed(blob: str) -> str | None:
    return next((p for p in _PREFIXES if blob.startswith(p)), None)


def _legacy(context: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in context.items() if k != "tenant_id"}
_NONCE = 12


class DecryptionError(Exception):
    """The value, its wrapped key or its context does not match. Never retry: alert."""


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise RuntimeError("encryption needs the 'cryptography' package (pip install cryptography)") from e
    return AESGCM(key)


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _aad(context: Mapping[str, str]) -> bytes:
    return json.dumps(dict(context), sort_keys=True, separators=(",", ":")).encode()


class KeyProvider(Protocol):
    def generate_data_key(self, context: Mapping[str, str]) -> tuple[bytes, bytes, str]:
        """(plaintext data key, wrapped data key, key id)."""
        ...

    def decrypt_data_key(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> bytes: ...


class LocalKeyProvider:
    """Key ring held in process memory. The active key wraps new data keys; every key in the
    ring can unwrap. Remove a key only after `rewrap` has moved everything off it."""

    def __init__(self, keys: Mapping[str, bytes], active: str):
        if active not in keys:
            raise ValueError(f"active key {active!r} is not in the key ring")
        for kid, k in keys.items():
            if len(k) != 32:
                raise ValueError(f"key {kid!r} must be 32 bytes (AES-256)")
        self.keys, self.active = dict(keys), active

    @classmethod
    def from_env(cls, spec: str) -> LocalKeyProvider:
        """FINBOT_LOCAL_KEYS="k2:<base64>,k1:<base64>". The first key is the active one."""
        pairs = [p.split(":", 1) for p in spec.split(",") if p.strip()]
        if not pairs or any(len(p) != 2 for p in pairs):
            raise ValueError("FINBOT_LOCAL_KEYS must be kid:base64key[,kid:base64key...]")
        return cls({kid.strip(): base64.b64decode(k) for kid, k in pairs}, pairs[0][0].strip())

    def generate_data_key(self, context: Mapping[str, str]) -> tuple[bytes, bytes, str]:
        dk = os.urandom(32)
        nonce = os.urandom(_NONCE)
        return dk, nonce + _aesgcm(self.keys[self.active]).encrypt(nonce, dk, _aad(context)), self.active

    def decrypt_data_key(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> bytes:
        if key_id not in self.keys:
            raise DecryptionError(f"key {key_id!r} is not in the key ring")
        try:
            return bytes(_aesgcm(self.keys[key_id]).decrypt(wrapped[:_NONCE], wrapped[_NONCE:], _aad(context)))
        except Exception:  # noqa: BLE001 - InvalidTag: wrong key, wrong context or tampering
            raise DecryptionError("data key does not unwrap") from None


class KmsKeyProvider:
    """AWS KMS. The IAM role needs kms:GenerateDataKey and kms:Decrypt on this key only, and a
    key policy condition on kms:EncryptionContext:purpose keeps each role to its own data."""

    def __init__(self, key_id: str, client: Any = None, region: str | None = None):
        if not key_id:
            raise ValueError("FINBOT_KMS_KEY_ID is required for FINBOT_ENCRYPTION=kms")
        if client is None:
            import boto3
            client = boto3.client("kms", region_name=region)
        self.key_id, self.kms = key_id, client

    def generate_data_key(self, context: Mapping[str, str]) -> tuple[bytes, bytes, str]:
        r = self.kms.generate_data_key(KeyId=self.key_id, KeySpec="AES_256", EncryptionContext=dict(context))
        return r["Plaintext"], r["CiphertextBlob"], r["KeyId"]

    def decrypt_data_key(self, wrapped: bytes, key_id: str, context: Mapping[str, str]) -> bytes:
        try:
            r = self.kms.decrypt(CiphertextBlob=wrapped, KeyId=key_id, EncryptionContext=dict(context))
        except Exception as e:  # noqa: BLE001 - InvalidCiphertextException, AccessDenied
            raise DecryptionError(f"KMS decrypt failed: {type(e).__name__}") from None
        return bytes(r["Plaintext"])


class EnvelopeCipher:
    def __init__(self, provider: KeyProvider, require: bool = False):
        self.provider, self.require = provider, require

    def seal(self, obj: Any, context: Mapping[str, str]) -> str:
        dk, wrapped, kid = self.provider.generate_data_key(context)
        nonce = os.urandom(_NONCE)
        ct = _aesgcm(dk).encrypt(nonce, json.dumps(obj, separators=(",", ":")).encode(), _aad(context))
        parts = (_b64e(kid.encode()), _b64e(wrapped), _b64e(nonce), _b64e(ct))
        return (PREFIX2 if "tenant_id" in context else PREFIX) + ".".join(parts)

    @staticmethod
    def _parts(blob: str) -> tuple[str, bytes, bytes, bytes]:
        try:
            kid, wrapped, nonce, ct = blob[len(_sealed(blob) or PREFIX):].split(".")
            return _b64d(kid).decode(), _b64d(wrapped), _b64d(nonce), _b64d(ct)
        except Exception:  # noqa: BLE001
            raise DecryptionError("malformed envelope") from None

    def open(self, blob: str, context: Mapping[str, str]) -> Any:
        prefix = _sealed(blob)
        if prefix is None:
            if self.require:
                raise DecryptionError("value is not encrypted and FINBOT_ENCRYPTION_REQUIRED is set")
            return json.loads(blob)
        if prefix == PREFIX:                      # sealed before tenant binding
            context = _legacy(context)
        kid, wrapped, nonce, ct = self._parts(blob)
        dk = self.provider.decrypt_data_key(wrapped, kid, context)
        try:
            return json.loads(_aesgcm(dk).decrypt(nonce, ct, _aad(context)))
        except Exception:  # noqa: BLE001
            raise DecryptionError("ciphertext or context was changed") from None

    def key_id(self, blob: str) -> str | None:
        """Which key wrapped this value (None if not sealed): for rotation reports."""
        return self._parts(blob)[0] if _sealed(blob) else None

    def rewrap(self, blob: str, context: Mapping[str, str]) -> str:
        return self.seal(self.open(blob, context), context)


class PlainCodec:
    """No encryption (FINBOT_ENCRYPTION=none): plain JSON, same interface as EnvelopeCipher."""

    require = False

    def seal(self, obj: Any, context: Mapping[str, str]) -> str:
        return json.dumps(obj)

    def open(self, blob: str, context: Mapping[str, str]) -> Any:
        if _sealed(blob):
            raise DecryptionError("value is encrypted but FINBOT_ENCRYPTION=none")
        return json.loads(blob)


Codec = EnvelopeCipher | PlainCodec


def make_codec(settings: Any) -> Codec:
    mode = settings.encryption
    if mode == "none":
        return PlainCodec()
    if mode == "local":
        return EnvelopeCipher(LocalKeyProvider.from_env(settings.local_keys), settings.encryption_required)
    if mode == "kms":
        return EnvelopeCipher(KmsKeyProvider(settings.kms_key_id, region=settings.aws_region),
                              settings.encryption_required)
    raise ValueError(f"unknown FINBOT_ENCRYPTION {mode!r} (known: none, local, kms)")


def session_tenant(session_id: str) -> str | None:
    """The tenant a session id names ("m_100.<uuid>" -> "m_100"), or None for an older id."""
    tenant, dot, rest = session_id.rpartition(".")
    return tenant if dot and tenant and rest else None


def ctx(session_id: str, purpose: str) -> dict[str, str]:
    """The encryption context used by the session stores: tenant (when the id names one),
    session and purpose."""
    tenant = session_tenant(session_id)
    return {**({"tenant_id": tenant} if tenant else {}), "session_id": session_id, "purpose": purpose}
