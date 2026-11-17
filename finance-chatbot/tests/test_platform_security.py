"""Platform security: encryption at rest, key rotation, revocation, agent delegation grants,
rate limits, DLP at the boundary, the tamper-evident audit log, the HTTP adapter, and
classic (non-prompt) injection at every input."""
import base64
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot import auditlog, dlp  # noqa: E402
from finbot.agent_identity import GrantDenied, GrantIssuer, effective_scopes  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.crypto import DecryptionError, EnvelopeCipher, LocalKeyProvider, PlainCodec, ctx  # noqa: E402
from finbot.http_api import App  # noqa: E402
from finbot.ratelimit import RateLimited, RateLimiter  # noqa: E402
from finbot.security import AuthError, Forbidden, HmacJwtVerifier, Revocations  # noqa: E402
from finbot.service import BadRequest  # noqa: E402
from finbot.store import SessionStore  # noqa: E402
from finbot.store_dynamo import DynamoSessionStore, InMemoryDynamo  # noqa: E402
from finbot.tools import Session  # noqa: E402

SECRET = "test-secret-that-is-at-least-32-characters-long"
OLD_SECRET = "the-previous-secret-also-at-least-32-chars"
DECLINED, XFER = "txn_A1B2C3D4", "xfer_R8S9T0U1"
PAN = "4111 1111 1111 1111"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def read_lines(path: str) -> list[str]:
    with open(path) as f:
        return f.read().splitlines()


def keys(*kids: str) -> LocalKeyProvider:
    ring = {k: bytes([i + 1]) * 32 for i, k in enumerate(kids)}
    return LocalKeyProvider(ring, kids[0])


def container(clock, store=None, **kw):
    base = {"llm_provider": "fake", "guardrail_provider": "local", "kb_path": "/nonexistent", "data_dir": "",
            "auth_mode": "hmac", "auth_secret": SECRET}
    c = Container(Settings(**{**base, **kw}), overrides={"store": store or SessionStore(clock=clock)}, clock=clock)
    c.chatbot.sleep = c.toolbox.sleep = lambda s: None
    return c


# --------------------------------------------------------------------- encryption at rest

class Encryption(unittest.TestCase):
    def test_round_trip_and_context_binding(self):
        c = EnvelopeCipher(keys("k1"))
        blob = c.seal({"a": 1}, ctx("s1", "session_state"))
        self.assertEqual(c.open(blob, ctx("s1", "session_state")), {"a": 1})
        with self.assertRaises(DecryptionError):        # copied into another session's row
            c.open(blob, ctx("s2", "session_state"))
        with self.assertRaises(DecryptionError):        # same session, different purpose
            c.open(blob, ctx("s1", "answer"))

    def test_tampering_is_detected(self):
        c = EnvelopeCipher(keys("k1"))
        blob = c.seal("hello", ctx("s1", "answer"))
        head, last = blob.rsplit(".", 1)
        raw = bytearray(base64.urlsafe_b64decode(last + "=" * (-len(last) % 4)))
        raw[0] ^= 1
        bad = head + "." + base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
        with self.assertRaises(DecryptionError):
            c.open(bad, ctx("s1", "answer"))
        with self.assertRaises(DecryptionError):
            c.open("fbenc1:garbage", ctx("s1", "answer"))

    def test_key_rotation(self):
        old = EnvelopeCipher(keys("k1"))
        blob = old.seal("v", ctx("s", "p"))
        ring = LocalKeyProvider({"k2": b"\x09" * 32, "k1": b"\x01" * 32}, "k2")
        new = EnvelopeCipher(ring)
        self.assertEqual(new.open(blob, ctx("s", "p")), "v")             # old key still decrypts
        moved = new.rewrap(blob, ctx("s", "p"))
        self.assertEqual((new.key_id(blob), new.key_id(moved)), ("k1", "k2"))
        retired = EnvelopeCipher(LocalKeyProvider({"k2": b"\x09" * 32}, "k2"))
        self.assertEqual(retired.open(moved, ctx("s", "p")), "v")
        with self.assertRaises(DecryptionError):                          # k1 removed: not rewrapped
            retired.open(blob, ctx("s", "p"))

    def test_plaintext_values_are_readable_until_encryption_is_required(self):
        self.assertEqual(EnvelopeCipher(keys("k1")).open('{"x": 1}', ctx("s", "p")), {"x": 1})
        with self.assertRaises(DecryptionError):
            EnvelopeCipher(keys("k1"), require=True).open('{"x": 1}', ctx("s", "p"))
        with self.assertRaises(DecryptionError):   # encryption switched off by mistake: fail loudly
            PlainCodec().open(EnvelopeCipher(keys("k1")).seal(1, ctx("s", "p")), ctx("s", "p"))

    def test_weak_or_missing_keys_rejected(self):
        with self.assertRaises(ValueError):
            LocalKeyProvider({"k1": b"short"}, "k1")
        with self.assertRaises(ValueError):
            LocalKeyProvider({"k1": b"\x01" * 32}, "k9")

    def test_kms_provider_passes_the_encryption_context(self):
        from finbot.crypto import KmsKeyProvider

        class FakeKms:
            def __init__(self):
                self.calls = []

            def generate_data_key(self, **kw):
                self.calls.append(("gen", kw))
                return {"Plaintext": b"\x07" * 32, "CiphertextBlob": b"wrapped", "KeyId": "arn:key/1"}

            def decrypt(self, **kw):
                self.calls.append(("dec", kw))
                return {"Plaintext": b"\x07" * 32}

        kms = FakeKms()
        c = EnvelopeCipher(KmsKeyProvider("alias/finbot", client=kms))
        self.assertEqual(c.open(c.seal("x", ctx("s1", "answer")), ctx("s1", "answer")), "x")
        self.assertEqual(kms.calls[0][1]["EncryptionContext"], {"session_id": "s1", "purpose": "answer"})
        self.assertEqual(kms.calls[0][1]["KeySpec"], "AES_256")
        self.assertEqual(kms.calls[1][1]["EncryptionContext"], {"session_id": "s1", "purpose": "answer"})


class EncryptedStores(unittest.TestCase):
    """Nothing a user typed, and nothing the bot answered, is readable in the database."""

    def _turn(self, store):
        clock = Clock()
        store.clock = clock
        c = container(clock, store)
        self.addCleanup(c.close)
        token = c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        sid = c.service.start(token)
        r = c.service.send(token, sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertIn("declined", r["answer"])
        again = c.service.send(token, sid, "msg-0001", f"why did {DECLINED} fail?")   # dedupe decrypts
        self.assertTrue(again["deduplicated"])
        self.assertEqual(again["answer"], r["answer"])
        return sid

    def test_sqlite_rows_hold_no_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.db")
            store = SessionStore(path, codec=EnvelopeCipher(keys("k1")))
            self._turn(store)
            raw = sqlite3.connect(path)
            self.addCleanup(raw.close)
            dump = " ".join(str(v) for row in raw.execute("SELECT state FROM sessions UNION ALL "
                                                          "SELECT user_text FROM turns UNION ALL "
                                                          "SELECT answer FROM turns UNION ALL "
                                                          "SELECT meta FROM turns") for v in row)
            self.assertNotIn(DECLINED, dump)
            self.assertNotIn("declined", dump)
            self.assertIn("fbenc2:", dump)   # tenant-bound envelope

    def test_dynamo_items_hold_no_plaintext(self):
        table = InMemoryDynamo()
        store = DynamoSessionStore(table, codec=EnvelopeCipher(keys("k1")))
        self._turn(store)
        dump = json.dumps(list(table.items.values()))
        self.assertNotIn(DECLINED, dump)
        self.assertIn("fbenc2:", dump)   # tenant-bound envelope


# --------------------------------------------------------------------- identity: rotation, revocation

class TokenLifecycle(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def test_key_rotation_accepts_previous_key_until_removed(self):
        old = HmacJwtVerifier(OLD_SECRET, clock=self.clock, kid="k1")
        tok = old.issue({"sub": "u", "tenant_id": "t", "role": "customer"})
        rotated = HmacJwtVerifier(SECRET, clock=self.clock, kid="k2", previous={"k1": OLD_SECRET})
        self.assertEqual(rotated.verify(tok).subject, "u")
        header = rotated.issue({"sub": "u", "tenant_id": "t", "role": "x"}).split(".")[0]
        self.assertEqual(json.loads(base64.urlsafe_b64decode(header + "=="))["kid"], "k2")   # new tokens: new key
        with self.assertRaisesRegex(AuthError, "unknown signing key"):
            HmacJwtVerifier(SECRET, clock=self.clock, kid="k2").verify(tok)

    def test_revoke_token_and_subject_across_pods(self):
        store = SessionStore(clock=self.clock)
        self.addCleanup(store.close)
        pod_a = HmacJwtVerifier(SECRET, clock=self.clock, revocations=Revocations(store, self.clock))
        pod_b = HmacJwtVerifier(SECRET, clock=self.clock, revocations=Revocations(store, self.clock))
        t1 = pod_a.issue({"sub": "u1", "tenant_id": "t", "role": "customer"})
        t2 = pod_a.issue({"sub": "u2", "tenant_id": "t", "role": "customer"})
        pod_b.verify(t1)
        jti = json.loads(base64.urlsafe_b64decode(t1.split(".")[1] + "=="))["jti"]
        pod_a.revocations.revoke_token(jti, self.clock() + 900)
        self.clock.t += 3                                   # past pod_b's cache
        with self.assertRaisesRegex(AuthError, "revoked"):
            pod_b.verify(t1)
        pod_b.verify(t2)
        pod_a.revocations.revoke_subject("u2")
        self.clock.t += 3
        with self.assertRaisesRegex(AuthError, "revoked"):
            pod_b.verify(t2)
        self.clock.t += 1
        pod_b.verify(pod_a.issue({"sub": "u2", "tenant_id": "t", "role": "customer"}))   # new sign-in works

    def test_only_admin_revokes_through_the_service(self):
        c = container(self.clock)
        self.addCleanup(c.close)
        user = c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        admin = c.verifier.issue({"sub": "ops", "tenant_id": "internal", "role": "admin"})
        with self.assertRaises(Forbidden):
            c.service.revoke(user, subject="u1")
        c.service.revoke(admin, subject="u1")
        with self.assertRaisesRegex(AuthError, "revoked"):
            c.service.start(user)


# --------------------------------------------------------------------- agent delegation grants

class AgentGrants(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.g = GrantIssuer(b"k" * 32, clock=self.clock)

    def test_grant_is_bound_to_tenant_session_scope_and_time(self):
        tok = self.g.mint("u1", "m_100", "s1", {"accounts:read"})
        self.assertEqual(self.g.verify(tok, tool="get_account", tenant_id="m_100", session_id="s1").actor,
                         "finbot-agent")
        for kw in ({"tool": "get_transfer", "tenant_id": "m_100", "session_id": "s1"},     # scope
                   {"tool": "get_account", "tenant_id": "m_200", "session_id": "s1"},      # tenant
                   {"tool": "get_account", "tenant_id": "m_100", "session_id": "s2"},      # session
                   {"tool": "delete_everything", "tenant_id": "m_100", "session_id": "s1"}):   # undeclared
            with self.assertRaises(GrantDenied):
                self.g.verify(tok, **kw)
        self.clock.t += 301
        with self.assertRaisesRegex(GrantDenied, "expired"):
            self.g.verify(tok, tool="get_account", tenant_id="m_100", session_id="s1")

    def test_agent_can_never_hold_a_write_scope(self):
        tok = self.g.mint("u1", "m_100", "s1", {"accounts:read", "accounts:write", "transfers:write"})
        claims = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))
        self.assertEqual(claims["scope"], "accounts:read")
        self.assertEqual(claims["act"], {"sub": "finbot-agent"})
        self.assertEqual(effective_scopes("customer", ["accounts:write"]), frozenset())
        self.assertEqual(effective_scopes("admin", None), frozenset())   # admins don't chat

    def test_forged_grant_rejected(self):
        other = GrantIssuer(b"x" * 32, clock=self.clock)
        with self.assertRaises(GrantDenied):
            self.g.verify(other.mint("u1", "m_100", "s1", {"read:*"}), tool="get_account", tenant_id="m_100",
                          session_id="s1")

    def test_token_scope_limits_what_the_bot_can_read(self):
        c = container(self.clock)
        self.addCleanup(c.close)
        narrow = c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer", "scope": "accounts:read"})
        sid = c.service.start(narrow)
        r = c.service.send(narrow, sid, "msg-0001", f"why was {XFER} returned?")
        self.assertNotIn("R02", r["answer"])                 # the transfer was never read
        self.assertIn("get_transfer", r["tools"])
        full = c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        sid2 = c.service.start(full)
        self.assertIn("returned", c.service.send(full, sid2, "msg-0002", f"why was {XFER} returned?")["answer"])

    def test_tool_runner_refuses_before_touching_the_backend(self):
        c = container(self.clock)
        self.addCleanup(c.close)
        calls = []
        real = c.toolbox._runner
        c.toolbox._runner = lambda s: (lambda n, a: calls.append(n) or real(s)(n, a))
        run = c.toolbox.handler(Session("s1", "m_100", ["support"], "customer", "u1", ["kb:read"]))
        out = run("get_account", {"account_id": "acct_M4N5P6Q7"})
        self.assertEqual((out["status"], out["source"]), ("refused", "authz"))
        self.assertEqual(calls, [])


# --------------------------------------------------------------------- rate limits

class RateLimits(unittest.TestCase):
    def test_user_and_tenant_buckets(self):
        clock = Clock()
        rl = RateLimiter(user_per_min=2, tenant_per_min=3, clock=clock)
        rl.check("a", "t")
        rl.check("a", "t")
        with self.assertRaises(RateLimited) as e:
            rl.check("a", "t")
        self.assertEqual(e.exception.scope, "user")
        self.assertGreater(e.exception.retry_after_s, 0)
        rl.check("b", "t")                                   # another user, same tenant
        with self.assertRaises(RateLimited) as e:
            rl.check("c", "t")
        self.assertEqual(e.exception.scope, "tenant")
        clock.t += 60
        rl.check("a", "t")                                   # refilled

    def test_service_limits_before_any_model_call(self):
        clock = Clock()
        c = container(clock, rate_user_per_min=1)
        self.addCleanup(c.close)
        tok = c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        sid = c.service.start(tok)
        with self.assertRaises(RateLimited):
            c.service.send(tok, sid, "msg-0001", "hi")


# --------------------------------------------------------------------- DLP at the boundary

class Dlp(unittest.TestCase):
    def test_categories(self):
        text = (f"card {PAN} cvv 123, ssn 123-45-6789, iban GB82 WEST 1234 5698 7654 32, "
                "routing 021000021 account number 12345678, mail a@b.co, call 415-555-0100, "
                "key AKIAABCDEFGHIJKLMNOP")
        out, rep = dlp.scan(text)
        for leaked in ("4111", "123-45-6789", "GB82", "021000021", "12345678", "AKIAABCDEFGHIJKLMNOP"):
            self.assertNotIn(leaked, out)
        self.assertIn("a@b.co", out)                          # detect only
        self.assertTrue(rep.pci)
        for cat in ("pci:card", "pci:cvv", "pii:ssn", "pii:iban", "pii:bank_account", "pii:email", "pii:phone",
                    "secret:aws_key"):
            self.assertIn(cat, rep.found)

    def test_lookalikes_left_alone(self):
        out, rep = dlp.scan("order 12345, routing 123456789 (bad checksum), amount 1,250.00 on 2026-11-12")
        self.assertEqual(out, "order 12345, routing 123456789 (bad checksum), amount 1,250.00 on 2026-11-12")
        self.assertFalse(rep.found)

    def test_every_request_and_response_is_scanned_and_audited(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as d:
            audit = os.path.join(d, "audit.jsonl")
            c = container(clock)
            c.audit_path = audit
            c.__dict__.pop("service", None)
            self.addCleanup(c.close)
            tok = c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
            sid = c.service.start(tok)
            leaky = c.worker.handle
            # a path that forgot to scan
            c.service.worker.handle = lambda *a, **kw: {**leaky(*a, **kw), "debug": f"raw card {PAN}"}
            r = c.service.send(tok, sid, "msg-0001", f"why did {DECLINED} fail? my card is {PAN}")
            self.assertNotIn("4111 1111", json.dumps(r))
            self.assertIn("pci:card", r["dlp"])
            rec = [json.loads(x) for x in read_lines(audit) if '"stream": "dlp"' in x][0]
            self.assertTrue(rec["egress_leak"])
            self.assertNotIn("4111", json.dumps(rec))


# --------------------------------------------------------------------- tamper-evident audit

class AuditChain(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = os.path.join(d.name, "a.jsonl")
        auditlog._state.pop(self.path, None)

    def _write(self, n=3):
        for i in range(n):
            auditlog.write(self.path, "turn", {"i": i})
        return read_lines(self.path)

    def test_intact_chain_verifies(self):
        ok, msg = auditlog.verify(self._write())
        self.assertTrue(ok, msg)

    def test_edit_delete_reorder_detected(self):
        lines = self._write()
        edited = lines[:1] + [lines[1].replace('"i": 1', '"i": 7')] + lines[2:]
        self.assertFalse(auditlog.verify(edited)[0])
        self.assertFalse(auditlog.verify([lines[0], lines[2]])[0])
        self.assertFalse(auditlog.verify([lines[1], lines[0], lines[2]])[0])

    def test_hmac_key_required_to_recompute(self):
        auditlog.configure("audit-key")
        self.addCleanup(auditlog.configure, None)
        lines = self._write()
        self.assertTrue(auditlog.verify(lines, "audit-key")[0])
        self.assertFalse(auditlog.verify(lines, None)[0])


# --------------------------------------------------------------------- HTTP adapter + classic injection

def call(app, method, path, body=None, token=None, headers=None, raw=None, ctype="application/json", query=""):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, "CONTENT_TYPE": ctype,
           "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
    if token:
        env["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    env.update(headers or {})
    out = {}

    def start(status, hdrs):
        out["status"], out["headers"] = int(status.split()[0]), dict(hdrs)
    out["body"] = json.loads(b"".join(app(env, start)))
    return out


class Http(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = container(self.clock)
        self.addCleanup(self.c.close)
        self.app = App(self.c.service)
        self.tok = self.c.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})

    def test_full_flow_and_security_headers(self):
        r = call(self.app, "POST", "/v1/sessions", {}, self.tok)
        self.assertEqual(r["status"], 201)
        sid = r["body"]["session_id"]
        r = call(self.app, "POST", f"/v1/sessions/{sid}/messages",
                 {"msg_id": "msg-0001", "text": f"why did {DECLINED} fail?"}, self.tok)
        self.assertEqual(r["status"], 200)
        self.assertIn("declined", r["body"]["answer"])
        for h in ("Strict-Transport-Security", "X-Content-Type-Options", "Content-Security-Policy", "Cache-Control"):
            self.assertIn(h, r["headers"])
        self.assertEqual(call(self.app, "GET", "/healthz")["status"], 200)

    def test_errors_do_not_leak_why(self):
        r = call(self.app, "POST", "/v1/sessions", {}, "bad.token.here")
        self.assertEqual((r["status"], r["body"]["error"]), (401, "unauthorized"))
        self.assertIn("WWW-Authenticate", r["headers"])
        self.assertIn("request_id", r["body"])

    def test_body_and_method_limits(self):
        sid = call(self.app, "POST", "/v1/sessions", {}, self.tok)["body"]["session_id"]
        path = f"/v1/sessions/{sid}/messages"
        self.assertEqual(call(self.app, "POST", path, raw=b"x" * 20000, token=self.tok)["status"], 413)
        self.assertEqual(call(self.app, "POST", path, {"msg_id": "m", "text": "t"}, self.tok,
                              ctype="text/plain")["status"], 415)
        self.assertEqual(call(self.app, "POST", path, raw=b"[1,2]", token=self.tok)["status"], 400)
        self.assertEqual(call(self.app, "POST", path, raw=b"{not json", token=self.tok)["status"], 400)
        self.assertEqual(call(self.app, "POST", path, {"msg_id": 5, "text": "t"}, self.tok)["status"], 400)
        self.assertEqual(call(self.app, "GET", path, token=self.tok)["status"], 405)
        # a token in the query string would land in access logs: refused outright
        self.assertEqual(call(self.app, "POST", "/v1/sessions", {}, query=f"token={self.tok}")["status"], 400)

    def test_rate_limit_maps_to_429_with_retry_after(self):
        c = container(self.clock, rate_user_per_min=1)
        self.addCleanup(c.close)
        app, tok = App(c.service), c.verifier.issue({"sub": "u9", "tenant_id": "m_100", "role": "customer"})
        call(app, "POST", "/v1/sessions", {}, tok)
        r = call(app, "POST", "/v1/sessions", {}, tok)
        self.assertEqual(r["status"], 429)
        self.assertIn("Retry-After", r["headers"])

    def test_header_injection_via_request_id(self):
        r = call(self.app, "GET", "/healthz", headers={"HTTP_X_REQUEST_ID": "abc12345\r\nSet-Cookie: x=1"})
        self.assertNotIn("\n", r["headers"]["X-Request-Id"])
        self.assertNotIn("Set-Cookie", r["headers"])

    def test_sql_injection_in_ids_never_reaches_a_query(self):
        tok = self.tok
        for evil in ("x' OR '1'='1", "1; DROP TABLE sessions;--", "../../etc/passwd"):
            r = call(self.app, "POST", f"/v1/sessions/{evil}/messages", {"msg_id": "msg-0001", "text": "hi"}, tok)
            self.assertEqual(r["status"], 404)
        sid = call(self.app, "POST", "/v1/sessions", {}, tok)["body"]["session_id"]
        r = call(self.app, "POST", f"/v1/sessions/{sid}/messages", {"msg_id": "x' OR 1=1--", "text": "hi"}, tok)
        self.assertEqual(r["status"], 400)
        # text is free-form: it is stored through bound parameters, so SQL in it is just text
        r = call(self.app, "POST", f"/v1/sessions/{sid}/messages",
                 {"msg_id": "msg-0002", "text": "'); DROP TABLE turns;-- what is code 51?"}, tok)
        self.assertEqual(r["status"], 200)
        self.assertEqual(self.c.store.tenant_of(sid), "m_100")      # tables intact
        self.assertEqual(len(self.c.store.turns(sid)), 1)

    def test_service_rejects_bad_ids_without_http_too(self):
        sid = self.c.service.start(self.tok)
        with self.assertRaises(BadRequest):
            self.c.service.send(self.tok, sid, "a' OR 1=1", "hi")
        with self.assertRaises(Forbidden):
            self.c.service.send(self.tok, "x' OR '1'='1", "msg-0001", "hi")

    def test_log_injection_cannot_forge_audit_records(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.jsonl")
            auditlog.write(path, "turn", {"user_text": 'hi"}\n{"stream": "control", "op": "drain"'})
            lines = read_lines(path)
            self.assertEqual(len(lines), 1)                  # newline stayed inside the JSON string
            self.assertEqual(json.loads(lines[0])["stream"], "turn")
            self.assertTrue(auditlog.verify(lines)[0])


class FetchAndSqlInjection(unittest.TestCase):
    def test_ingestion_fetches_https_only(self):
        import urllib.request

        from finbot.ingestion.sources import _HttpsOnlyRedirects, _open, _require_https
        for bad in ("file:///etc/passwd", "http://169.254.169.254/latest/meta-data/", "ftp://x/y", "https:///nohost"):
            with self.assertRaises(ValueError):
                _require_https(bad)
            with self.assertRaises(ValueError):
                _open(urllib.request.Request(bad), 1)
        _require_https("https://docs.example.com/page")
        req = urllib.request.Request("https://docs.example.com/a")
        with self.assertRaises(ValueError):          # a redirect to plain http or file is refused
            _HttpsOnlyRedirects().redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")

    def test_vector_table_name_must_be_an_identifier(self):
        from finbot.ingestion.vectorstores import PgVectorStore
        with self.assertRaisesRegex(ValueError, "invalid table name"):
            PgVectorStore("postgresql://unused", table="kb; DROP TABLE users")


if __name__ == "__main__":
    unittest.main()
