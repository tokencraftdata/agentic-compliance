"""HTTP adapter around ChatService: a plain WSGI app (stdlib only).

Serve it behind the ALB with any WSGI server, for example
    gunicorn 'finbot.http_api:app_from_env()' -b 0.0.0.0:8080
or locally: python -m finbot.http_api

    GET  /healthz                              liveness for the ALB target group
    POST /v1/sessions                          start a session        -> {"session_id"}
    POST /v1/sessions/<id>/messages            {"msg_id", "text"}     -> the turn's result
    POST /v1/admin/revocations                 {"jti", "expires_at"} or {"subject"} (admin)

The edge (ALB + WAF, infra/) terminates TLS and drops known-bad traffic. This layer does
not trust it to: it enforces its own limits and treats every header and body as hostile.

    * Bearer token only from the Authorization header; never from a query string, where
      it would land in access logs.
    * JSON bodies only, at most MAX_BODY bytes; read with an exact length, never to EOF.
    * Path parameters and the request id must match strict patterns. A request id with
      CR/LF or anything unexpected is replaced, so it cannot split headers or forge
      log lines.
    * Errors return a fixed message and the request id, never a stack trace or the
      reason a token failed (that goes to the log).
    * Security headers on every response; responses are never cached.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from .crypto import DecryptionError
from .ratelimit import RateLimited
from .security import AuthError, Forbidden
from .service import BadRequest, ChatService
from .store import LeaseHeld, StaleWrite

log = logging.getLogger("finbot.http")

MAX_BODY = 16 * 1024
SESSION = re.compile(r"^/v1/sessions/([A-Za-z0-9_.-]{8,128})/messages$")
CANCEL = re.compile(r"^/v1/sessions/([A-Za-z0-9_.-]{8,128})/cancel$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SECURITY_HEADERS = [
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
]
STATUS = {200: "200 OK", 201: "201 Created", 202: "202 Accepted", 400: "400 Bad Request", 401: "401 Unauthorized",
          403: "403 Forbidden", 404: "404 Not Found", 405: "405 Method Not Allowed", 409: "409 Conflict",
          413: "413 Payload Too Large", 415: "415 Unsupported Media Type", 429: "429 Too Many Requests",
          500: "500 Internal Server Error", 503: "503 Service Unavailable"}

StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class HttpError(Exception):
    def __init__(self, code: int, message: str, headers: list[tuple[str, str]] | None = None):
        super().__init__(message)
        self.code, self.message, self.headers = code, message, headers or []


def _token(environ: dict) -> str:
    auth = environ.get("HTTP_AUTHORIZATION", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("missing bearer token")
    return token.strip()


def _body(environ: dict) -> dict:
    ctype = environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HttpError(415, "send application/json")
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise HttpError(400, "bad Content-Length") from None
    if length > MAX_BODY:
        raise HttpError(413, f"body is limited to {MAX_BODY} bytes")
    raw = environ["wsgi.input"].read(length) if length > 0 else b""
    try:
        body = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HttpError(400, "body is not valid JSON") from None
    if not isinstance(body, dict):
        raise HttpError(400, "body must be a JSON object")
    return body


def _str(body: dict, key: str) -> str:
    v = body.get(key, "")
    if not isinstance(v, str):
        raise HttpError(400, f"{key} must be a string")
    return v


class App:
    def __init__(self, service: ChatService):
        self.service = service

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        rid = environ.get("HTTP_X_REQUEST_ID", "")
        if not REQUEST_ID.match(rid):
            rid = uuid.uuid4().hex
        try:
            code, payload, extra = self._route(environ)
        except HttpError as e:
            code, payload, extra = e.code, {"error": e.message}, e.headers
        except AuthError as e:
            log.info("auth failed rid=%s: %s", rid, e)
            code, payload, extra = 401, {"error": "unauthorized"}, [("WWW-Authenticate", 'Bearer realm="finbot"')]
        except Forbidden:
            code, payload, extra = 403, {"error": "forbidden"}, []
        except BadRequest as e:
            code, payload, extra = 400, {"error": str(e)}, []
        except RateLimited as e:
            code, payload, extra = 429, {"error": "rate limited", "retry_after_s": e.retry_after_s}, \
                [("Retry-After", str(int(e.retry_after_s)))]
        except (LeaseHeld, StaleWrite):
            code, payload, extra = 409, {"error": "this session is busy; resend the same msg_id"}, \
                [("Retry-After", "1")]
        except DecryptionError:
            log.error("decryption failure rid=%s", rid)    # tampering or a key problem: alert
            code, payload, extra = 500, {"error": "internal error"}, []
        except Exception:  # noqa: BLE001 - never leak internals to the caller
            log.exception("unhandled rid=%s", rid)
            code, payload, extra = 500, {"error": "internal error"}, []
        if code == 200 and isinstance(payload, dict) and payload.get("blocked_by") == "unavailable":
            code = 503
            extra = [("Retry-After", str(int(payload.get("retry_after_s", 30))))]
        if code >= 400:
            payload = {**payload, "request_id": rid}
        data = json.dumps(payload).encode()
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(data))),
                   ("X-Request-Id", rid), *SECURITY_HEADERS, *extra]
        start_response(STATUS[code], headers)
        return [data]

    def _route(self, environ: dict) -> tuple[int, Any, list[tuple[str, str]]]:
        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "")
        if path == "/healthz":
            return 200, {"ok": True}, []
        if environ.get("QUERY_STRING"):
            raise HttpError(400, "query parameters are not accepted")
        if method != "POST":
            raise HttpError(405, "use POST", [("Allow", "POST")])
        if path == "/v1/sessions":
            return 201, {"session_id": self.service.start(_token(environ))}, []
        m = SESSION.match(path)
        if m:
            token, body = _token(environ), _body(environ)
            result = self.service.send(token, m.group(1), _str(body, "msg_id"), _str(body, "text"))
            if result.get("pending"):   # queue mode: not committed yet; resend the same msg_id
                return 202, result, [("Retry-After", str(result.get("retry_after_s", 2)))]
            return 200, result, []
        m = CANCEL.match(path)
        if m:
            token, body = _token(environ), _body(environ)
            return 202, self.service.cancel(token, m.group(1), _str(body, "msg_id")), []
        if path == "/v1/admin/revocations":
            token, body = _token(environ), _body(environ)
            exp = body.get("expires_at", 0)
            if not isinstance(exp, (int, float)):
                raise HttpError(400, "expires_at must be a number")
            return 200, self.service.revoke(token, jti=_str(body, "jti"), expires_at=float(exp),
                                            subject=_str(body, "subject")), []
        raise HttpError(404, "not found")


def app_from_env() -> App:
    from .container import Container
    return App(Container(audit_path="-").service)


if __name__ == "__main__":   # local only: wsgiref is single-threaded and has no TLS
    from wsgiref.simple_server import make_server
    with make_server("127.0.0.1", 8080, app_from_env()) as srv:
        print("finbot http on http://127.0.0.1:8080")
        srv.serve_forever()
