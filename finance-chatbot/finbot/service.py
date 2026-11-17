"""Service layer (facade): the only entry point an HTTP handler, Lambda or queue
consumer calls. It owns the trust boundary:

    token -> Principal (verified, not revoked) -> rate limit -> authorize -> validate input
          -> DLP scan of the request -> worker -> DLP scan of the response
                                      (deny by default)

Nothing below this layer accepts tenant, role, groups or scopes from a request body.
Errors map to transport codes: AuthError 401, Forbidden 403 (also for unknown
sessions, so callers cannot probe which session ids exist), BadRequest 400,
RateLimited 429 with retry_after_s, LeaseHeld 409 (retry), an unavailable turn 503
with retry_after_s. finbot/http_api.py does that mapping.
"""
from __future__ import annotations

import hmac
import re

from . import auditlog, dlp
from .control import ControlPlane
from .ratelimit import RateLimiter
from .runtime import Worker
from .security import Authorizer, Forbidden, Principal, Revocations
from .store import SessionExpired
from .tenancy import TenantViolation, tenant_scope

MSG_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_TEXT = 4000


class BadRequest(Exception):
    pass


class ChatService:
    def __init__(self, worker: Worker, verifier, control: ControlPlane, authorizer: Authorizer | None = None,
                 limiter: RateLimiter | None = None, revocations: Revocations | None = None,
                 audit_path: str | None = None, dispatcher=None):
        self.worker, self.verifier, self.control = worker, verifier, control
        # inline: run the turn here; queue: enqueue it for a worker and wait for the commit (dispatch.py)
        from .dispatch import InlineDispatcher
        self.dispatch = dispatcher or InlineDispatcher(worker)
        self.authz = authorizer or Authorizer()
        self.limiter, self.revocations, self.audit_path = limiter, revocations, audit_path

    def principal(self, token: str) -> Principal:
        return self.verifier.verify(token)  # AuthError -> 401

    def _limited(self, token: str) -> Principal:
        p = self.principal(token)
        if self.limiter is not None:
            self.limiter.check(p.subject, p.tenant_id)   # RateLimited -> 429
        return p

    # ------------------------------------------------------------------ chat

    def start(self, token: str) -> str:
        p = self._limited(token)
        self.authz.can_chat(p)
        if not p.subject:
            # A session is owned by the user who starts it; with no user there is no owner, and
            # anyone in the tenant could talk in it.
            raise Forbidden("token has no subject")
        with tenant_scope(p.tenant_id):          # data access below runs for the caller's tenant only
            return self.worker.start_session(p.tenant_id, list(p.groups), p.role, p.subject,
                                             list(p.scopes) or None)

    def send(self, token: str, session_id: str, msg_id: str, text: str) -> dict:
        p = self._limited(token)
        with tenant_scope(p.tenant_id):
            return self._send(p, session_id, msg_id, text)

    def _send(self, p: Principal, session_id: str, msg_id: str, text: str) -> dict:
        saved = self._own(p, session_id)
        # History and cached replies were produced under these permissions. Never replay them
        # under a different authorization snapshot, even if the subject and tenant still match.
        if (p.role != saved.get("role") or set(p.groups) != set(saved.get("user_groups", []))
                or set(p.scopes) != set(saved.get("scopes") or [])):
            raise Forbidden("session permissions changed; start a new session")
        if not MSG_ID.match(msg_id or ""):
            raise BadRequest("msg_id must be 8-64 chars of [A-Za-z0-9_-] (client-generated, reused on resend)")
        text = (text or "").strip()
        if not text or len(text) > MAX_TEXT:
            raise BadRequest(f"text must be 1-{MAX_TEXT} characters")
        text, inbound = dlp.scan(text)
        result, outbound = dlp.scan(self.dispatch.run(p.tenant_id, session_id, msg_id, text))
        self._audit_dlp(p, session_id, msg_id, inbound, outbound)
        if inbound.found:
            result["dlp"] = sorted(inbound.found)   # the client can tell the user not to send card data
        return result

    def cancel(self, token: str, session_id: str, msg_id: str) -> dict:
        """Stop a turn in flight (or one still queued). The owner of the session only."""
        p = self._limited(token)
        with tenant_scope(p.tenant_id):
            self._own(p, session_id)
            if not MSG_ID.match(msg_id or ""):
                raise BadRequest("msg_id must be 8-64 chars of [A-Za-z0-9_-]")
            self.worker.cancel(session_id, msg_id, by=p.subject)
        return {"cancelled": msg_id}

    def _audit_dlp(self, p: Principal, session_id: str, msg_id: str, inbound: dlp.DlpReport,
                   outbound: dlp.DlpReport) -> None:
        """Categories and counts only, never values. Card data in a response means an internal
        path failed to mask it: `egress_leak` is the alert."""
        if not inbound.found and not outbound.found:
            return
        auditlog.write(self.audit_path, "dlp", {
            "subject": p.subject, "tenant_id": p.tenant_id, "session_id": session_id, "msg_id": msg_id,
            "request": inbound.as_dict(), "response": outbound.as_dict(), "egress_leak": outbound.pci})
        events = self.worker.bot.security.events
        if inbound.found:
            events.emit("sensitive_data", "low", tenant=p.tenant_id, session_id=session_id, subject=p.subject,
                        resource="user_message", decision="masked", reasons=sorted(inbound.found))
        if outbound.pci:   # an internal path failed to mask card data
            events.emit("egress_leak", "critical", tenant=p.tenant_id, session_id=session_id, subject=p.subject,
                        resource="answer", decision="masked", reasons=sorted(outbound.found))

    # ------------------------------------------------------------------ token revocation (admin)

    def revoke(self, token: str, *, jti: str = "", expires_at: float = 0, subject: str = "") -> dict:
        p = self.principal(token)
        self.authz.can_revoke(p)
        if self.revocations is None:
            raise BadRequest("revocation is not configured")
        if jti:
            self.revocations.revoke_token(jti, expires_at)
        if subject:
            self.revocations.revoke_subject(subject)
        if not jti and not subject:
            raise BadRequest("give jti or subject")
        auditlog.write(self.audit_path, "control", {"actor": p.subject, "op": "revoke", "jti": jti,
                                                    "subject": subject})
        return {"revoked": {"jti": jti, "subject": subject}}

    def _own(self, p: Principal, session_id: str) -> dict:
        try:
            tenant = self.worker.store.tenant_of(session_id)
        except (KeyError, SessionExpired, TenantViolation):   # another tenant's session looks missing
            raise Forbidden("session not found or not yours") from None
        self.authz.can_use_session(p, tenant)
        # A session belongs to the user who started it, not to the whole tenant: otherwise anyone in
        # the tenant who learns a session id could talk in it with that session's role and scopes.
        saved = self.worker.store.load(session_id)[1].get("session", {})
        owner = saved.get("subject") or ""
        if not owner or not p.subject or not hmac.compare_digest(owner, p.subject):
            raise Forbidden("session not found or not yours")
        return saved

    # ------------------------------------------------------------------ control plane

    def models_status(self, token: str) -> dict:
        return self.control.status(self.principal(token))

    def drain_model(self, token: str, name: str, reason: str) -> dict:
        return self.control.drain(self.principal(token), name, reason)

    def restore_model(self, token: str, name: str, reason: str = "") -> dict:
        return self.control.restore(self.principal(token), name, reason)

    def set_routing(self, token: str, mode: str, reason: str) -> dict:
        return self.control.set_routing(self.principal(token), mode, reason)
