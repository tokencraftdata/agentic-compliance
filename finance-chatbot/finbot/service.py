"""Service layer (facade): the only entry point an HTTP handler, Lambda or queue
consumer calls. It owns the trust boundary:

    token -> Principal (verified)  -> authorize -> validate input -> worker
                                      (deny by default)

Nothing below this layer accepts tenant, role or groups from a request body.
Errors map to transport codes: AuthError 401, Forbidden 403 (also for unknown
sessions, so callers cannot probe which session ids exist), BadRequest 400,
LeaseHeld 409 (retry), an unavailable turn 503 with retry_after_s.
"""
from __future__ import annotations

import re

from .control import ControlPlane
from .runtime import Worker
from .security import Authorizer, Forbidden, Principal
from .store import SessionExpired

MSG_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_TEXT = 4000


class BadRequest(Exception):
    pass


class ChatService:
    def __init__(self, worker: Worker, verifier, control: ControlPlane, authorizer: Authorizer | None = None):
        self.worker, self.verifier, self.control = worker, verifier, control
        self.authz = authorizer or Authorizer()

    def principal(self, token: str) -> Principal:
        return self.verifier.verify(token)  # AuthError -> 401

    # ------------------------------------------------------------------ chat

    def start(self, token: str) -> str:
        p = self.principal(token)
        self.authz.can_chat(p)
        return self.worker.start_session(p.tenant_id, list(p.groups), p.role)

    def send(self, token: str, session_id: str, msg_id: str, text: str) -> dict:
        p = self.principal(token)
        self._own(p, session_id)
        if not MSG_ID.match(msg_id or ""):
            raise BadRequest("msg_id must be 8-64 chars of [A-Za-z0-9_-] (client-generated, reused on resend)")
        text = (text or "").strip()
        if not text or len(text) > MAX_TEXT:
            raise BadRequest(f"text must be 1-{MAX_TEXT} characters")
        return self.worker.handle(session_id, msg_id, text)

    def _own(self, p: Principal, session_id: str) -> None:
        try:
            tenant = self.worker.store.tenant_of(session_id)
        except (KeyError, SessionExpired):
            raise Forbidden("session not found or not yours") from None
        self.authz.can_use_session(p, tenant)

    # ------------------------------------------------------------------ control plane

    def models_status(self, token: str) -> dict:
        return self.control.status(self.principal(token))

    def drain_model(self, token: str, name: str, reason: str) -> dict:
        return self.control.drain(self.principal(token), name, reason)

    def restore_model(self, token: str, name: str, reason: str = "") -> dict:
        return self.control.restore(self.principal(token), name, reason)

    def set_routing(self, token: str, mode: str, reason: str) -> dict:
        return self.control.set_routing(self.principal(token), mode, reason)
