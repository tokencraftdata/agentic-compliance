"""Draws the five article diagrams as SVG; export.sh turns them into 2x PNGs for Medium and Substack.

    python3 docs/diagrams/make.py && sh docs/diagrams/export.sh
"""
from __future__ import annotations

import os
from html import escape

OUT = os.path.dirname(os.path.abspath(__file__))
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
INK, MUTED, LINE, BG = "#1f2937", "#4b5563", "#9ca3af", "#ffffff"
C = {  # stroke, fill
    "code": ("#047857", "#ecfdf5"), "model": ("#1d4ed8", "#eff6ff"), "data": ("#6d28d9", "#f5f3ff"),
    "people": ("#b45309", "#fffbeb"), "block": ("#b91c1c", "#fef2f2"), "grey": ("#6b7280", "#f9fafb"),
}


class Svg:
    def __init__(self, w: int, h: int):
        self.w, self.h, self.parts = w, h, []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, size=18, weight="normal", color=INK, anchor="start", italic=False):
        style = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
                 f'fill="{color}" text-anchor="{anchor}"{style}>{escape(s)}</text>')

    def rich(self, x, y, runs, size=18, anchor="start"):
        """runs: [(text, weight, color)]"""
        spans = "".join(f'<tspan font-weight="{w}" fill="{c}">{escape(t)}</tspan>' for t, w, c in runs)
        self.add(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" text-anchor="{anchor}">{spans}</text>')

    def box(self, x, y, w, h, kind="grey", r=10, dash=False, width=2):
        stroke, fill = C[kind]
        d = ' stroke-dasharray="8 6"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{width}"{d}/>')

    def card(self, x, y, w, h, kind, title, lines, title_size=20, line_size=16, gap=24):
        self.box(x, y, w, h, kind)
        self.text(x + 18, y + 32, title, title_size, "bold", C[kind][0])
        for i, line in enumerate(lines):
            self.text(x + 18, y + 32 + gap * (i + 1) + 2, line, line_size, color=INK)

    def arrow(self, pts, color=LINE, dash=False, label=None, lx=None, ly=None, width=2.5):
        d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
        da = ' stroke-dasharray="7 6"' if dash else ""
        mid = {"#9ca3af": "a-grey", C["code"][0]: "a-code", C["block"][0]: "a-block",
               C["model"][0]: "a-model", C["people"][0]: "a-people", C["data"][0]: "a-data"}[color]
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{da} marker-end="url(#{mid})"/>')
        if label:
            self.text(lx, ly, label, 15, color=MUTED)

    def save(self, name: str) -> None:
        markers = "".join(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{col}"/></marker>'
            for mid, col in [("a-grey", LINE), ("a-code", C["code"][0]), ("a-block", C["block"][0]),
                             ("a-model", C["model"][0]), ("a-people", C["people"][0]), ("a-data", C["data"][0])])
        body = "\n".join(self.parts)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}"><defs>{markers}</defs>'
               f'<rect width="{self.w}" height="{self.h}" fill="{BG}"/>\n{body}\n</svg>\n')
        with open(os.path.join(OUT, name), "w") as f:
            f.write(svg)


def legend(s: Svg, y: int) -> None:
    items = [("code", "code decides"), ("model", "model writes"), ("data", "records and documents"),
             ("people", "people act"), ("block", "blocked or refused")]
    x = 40
    for kind, label in items:
        s.box(x, y - 16, 22, 22, kind, r=5)
        s.text(x + 32, y + 1, label, 16, color=MUTED)
        x += 48 + len(label) * 8.6
    s.text(40, y + 34, "finbot · open source · synthetic data · github.com/tokencraftdata/finbot", 15, color=MUTED)


# ------------------------------------------------------------------ 1. architecture

def architecture() -> None:
    s = Svg(1200, 1476)
    s.text(40, 54, "finbot: the model writes, code decides", 32, "bold")
    s.text(40, 88, "A fintech support bot on Amazon Bedrock. Every claim about someone's money is checked "
                   "against the records first.", 18, color=MUTED)

    s.card(40, 120, 330, 70, "people", "Customer or support agent", [], title_size=20)
    s.text(58, 176, "asks about an account, transfer, card, loan …", 15, color=MUTED)
    s.card(830, 120, 330, 70, "grey", "Identity provider (OIDC)", [], title_size=20)
    s.text(848, 176, "signs the token the API verifies", 15, color=MUTED)

    s.box(40, 220, 1120, 74, "code", dash=True)
    boundary = "API verifies the token; tenant, role and groups come from it, never from the chat text"
    s.rich(60, 252, [("Trust boundary  ", "bold", C["code"][0]), (boundary, "normal", INK)], 18)
    s.text(60, 280, "then: per-session FIFO turn queue (SQS) · de-duplicated by message id", 16, color=MUTED)
    s.arrow([(205, 190), (205, 218)])
    s.arrow([(995, 190), (995, 218)], label="public keys (JWKS)", lx=1005, ly=210)

    # worker pipeline
    s.box(40, 330, 680, 760, "grey")
    s.text(60, 364, "Stateless worker: one turn, in this order", 21, "bold")
    s.arrow([(380, 294), (380, 328)])
    stages = [
        ("code", "1  Mask card data", "before anything else sees the text"),
        ("code", "2  Risk lock", "a session that kept attacking goes to a person"),
        ("code", "3  Input guard", "prompt attacks, denied topics, PII · Bedrock Guardrails"),
        ("code", "4  Language", "every check reads English; other languages go to a person"),
        ("code", "5  Learned guard policy", "escalate complaints and hardship; block learned attacks"),
        ("code", "6  Focus", "which record this turn is about, decided in code"),
        ("people", "7  Change request?", "refund, cancel, unblock, appeal … becomes a ticket"),
        ("model", "8  Claude + read-only tools", "drafts an answer from tool results only"),
        ("block", "9  Hallucination gate", "verify · one repair · answer built from the record · refuse"),
        ("code", "10  Output guard", "grounding, PII, prompt-leak canary"),
        ("code", "11  Commit the turn", "compare-and-swap on version and lease · audit line"),
    ]
    y = 384
    for kind, title, detail in stages:
        s.box(60, y, 640, 56, kind, r=8)
        s.text(78, y + 24, title, 18, "bold", C[kind][0])
        s.text(78, y + 46, detail, 15.5, color=INK)
        y += 62
    stage8 = 384 + 7 * 62 + 28

    # right column
    s.card(760, 330, 400, 170, "model", "Model engine",
           ["Claude on Amazon Bedrock", "failover: us-east-1 → us-west-2 → Claude API",
            "circuit breaker per model · 90 s turn deadline", "a backup model gets the same checks"])
    s.arrow([(700, stage8), (730, stage8), (730, 415), (758, 415)], color=C["model"][0])
    s.card(760, 530, 400, 150, "code", "Read-only tools",
           ["scoped to the tenant in the token", "only ids the user typed (provenance)",
            "fields cut to an allow-list", "results screened, wrapped as data"])
    s.arrow([(960, 500), (960, 528)], color=C["model"][0])

    s.box(760, 710, 400, 250, "data")
    s.text(778, 742, "Domain plug-ins (FINBOT_DOMAINS)", 20, "bold", C["data"][0])
    chips = ["accounts", "transfers", "kyc", "lending", "issuing", "onboarding",
             "disputes", "mortgages", "bnpl", "insurance", "subscriptions", "payments"]
    for i, name in enumerate(chips):
        cx, cy = 778 + (i % 3) * 124, 758 + (i // 3) * 48
        s.add(f'<rect x="{cx}" y="{cy}" width="114" height="38" rx="19" fill="#ffffff" stroke="{C["data"][0]}" '
              f'stroke-width="1.5"/>')
        s.text(cx + 57, cy + 25, name, 15.5, color=INK, anchor="middle")
    s.arrow([(960, 680), (960, 708)], color=C["data"][0])

    s.card(760, 990, 400, 100, "data", "Systems of record and help pages",
           ["core banking · cards · KYC · loans · billing · claims", "indexed help pages, cited by URL"],
           line_size=15.5)
    s.arrow([(960, 960), (960, 988)], color=C["data"][0])

    # bottom row
    bottom = [
        ("grey", "Session store", ["DynamoDB, one versioned record", "owner lease, compare-and-swap",
                                   "a crashed turn commits nothing", "typed ledger per record"]),
        ("code", "Adaptive guardrails", ["a reviewer labels a mistake", "rule proposed, then gated on evals",
                                         "shadow → canary → active", "admin approves; rollback in one call"]),
        ("people", "People and tickets", ["the bot executes nothing", "tickets for every change request",
                                          "handoff after 2 unverifiable answers", "escalation by policy"]),
        ("data", "Knowledge ingestion", ["sources → heading-aware chunks", "screened for injection",
                                         "Titan embeddings → vector DB", "feedback never auto-ingested"]),
    ]
    for i, (kind, title, lines) in enumerate(bottom):
        x = 40 + i * 285
        s.card(x, 1140, 265, 170, kind, title, lines, title_size=19, line_size=15, gap=29)
    s.arrow([(172, 1092), (172, 1138)], label="load / commit", lx=182, ly=1122)
    s.arrow([(457, 1138), (457, 1092)], color=C["code"][0], label="learned rules", lx=467, ly=1122)
    s.arrow([(650, 1092), (650, 1138)], color=C["people"][0], label="tickets", lx=588, ly=1122)
    s.arrow([(1027, 1138), (1027, 1092)], color=C["data"][0], label="help pages", lx=1037, ly=1122)

    s.text(40, 1356, "Stages 1–7 and 9–11 are plain Python: the same input gets the same decision every time. "
                     "Only stage 8 uses the model.", 16.5, color=MUTED)
    legend(s, 1404)
    s.save("finbot-architecture.svg")


# ------------------------------------------------------------------ 2. the gate, with an example

def gate() -> None:
    s = Svg(1200, 1276)
    s.text(40, 54, "One answer through the hallucination gate", 32, "bold")
    s.text(40, 88, "The model's answer is a draft. Code checks it against this turn's records before anyone "
                   "sees it.", 18, color=MUTED)

    x, w = 40, 520
    s.card(x, 120, w, 74, "model", "Draft answer from Claude", [], title_size=20)
    s.text(x + 18, 180, "written from the tool results of this turn", 15.5, color=INK)
    s.arrow([(300, 194), (300, 222)])

    s.box(x, 224, w, 330, "code")
    s.text(x + 18, 256, "Verifier chain (plain Python, no second model)", 20, "bold", C["code"][0])
    checks = [
        ("Evidence", "amounts, currencies, dates, codes, ids, links"),
        ("Reason", "a cause in plain words matches the record's code"),
        ("Domain", "status, cause and reason keys of every record"),
        ("Policy", "no promises the bot can't keep: refunds, guarantees"),
        ("Tickets", "no ticket the code didn't open"),
        ("Language", "English, the language every check reads"),
    ]
    for i, (name, what) in enumerate(checks):
        yy = 292 + i * 42
        s.add(f'<circle cx="{x + 30}" cy="{yy - 6}" r="6" fill="{C["code"][0]}"/>')
        s.rich(x + 46, yy, [(name + "  ", "bold", INK), (what, "normal", INK)], 16.5)
    s.text(x + 18, 540, "every verifier runs; any one can block", 15, color=MUTED, italic=True)

    def step(y, kind, title, detail, detail2=None):
        s.box(x, y, w, 92 if detail2 else 74, kind)
        s.text(x + 18, y + 30, title, 19, "bold", C[kind][0])
        s.text(x + 18, y + 56, detail, 15.5, color=INK)
        if detail2:
            s.text(x + 18, y + 78, detail2, 15.5, color=INK)

    s.arrow([(300, 554), (300, 590)], color=C["block"][0], label="fails", lx=310, ly=578)
    step(592, "model", "1. One guided repair", "the model is told exactly which claims failed;",
         "re-checked against the repair's own tool calls")
    s.arrow([(300, 684), (300, 720)], color=C["block"][0], label="fails again", lx=310, ly=708)
    step(722, "code", "2. Answer built from the record", "code states validated fields and the policy next step;",
         "checked by the same chain")
    s.arrow([(300, 814), (300, 850)], color=C["block"][0], label="no single record to state", lx=310, ly=838)
    step(852, "block", "3. “I can't verify that, so I won't guess.”", "two in a row: handed to a person "
                                                                             "with a reference")
    # pass path
    s.box(600, 224, 560, 80, "code")
    s.text(618, 256, "Passes", 20, "bold", C["code"][0])
    s.text(618, 284, "→ output guard → the user sees it", 16.5, color=INK)
    s.arrow([(560, 300), (598, 270)], color=C["code"][0])
    s.arrow([(560, 640), (580, 640), (580, 290), (598, 290)], color=C["code"][0], dash=True)
    s.arrow([(560, 770), (586, 770), (586, 296), (598, 296)], color=C["code"][0], dash=True)

    # worked example
    ex = 600
    s.box(ex, 340, 560, 600, "grey")
    s.text(ex + 18, 372, "Worked example (synthetic data)", 20, "bold")
    s.rich(ex + 18, 410, [("User  ", "bold", C["people"][0]), ("Will claim clm_R5S4T3U2 be approved?", "normal", INK)],
           16.5)
    s.box(ex + 18, 428, 524, 128, "data", r=8, width=1.5)
    s.text(ex + 34, 454, "get_claim  →  record", 16, "bold", C["data"][0])
    s.text(ex + 34, 478, "status: in_review · claimed: 18500.00 USD", 15.5)
    s.text(ex + 34, 500, "policy_type: business_property", 15.5)
    s.add(f'<line x1="{ex + 34}" y1="{518}" x2="{ex + 526}" y2="{518}" stroke="{LINE}" stroke-dasharray="4 4"/>')
    s.text(ex + 34, 542, "adjuster_notes, fraud_score: stripped, the model never sees them", 15,
           color=C["block"][0])
    s.rich(ex + 18, 590, [("Draft  ", "bold", C["model"][0]),
                          ("“Good news, it was approved. You're covered.”", "normal", INK)], 16.5)
    s.box(ex + 18, 606, 524, 96, "block", r=8, width=1.5)
    s.text(ex + 34, 632, "Blocked", 16, "bold", C["block"][0])
    s.text(ex + 34, 656, "status_mismatch:claim:record=in_review,claimed=approved", 15)
    s.text(ex + 34, 680, "reason_mismatch:claimed=coverage_opinion", 15)
    s.text(ex + 18, 734, "After one repair", 16.5, "bold", C["code"][0])
    for i, line in enumerate(["“Claim clm_R5S4T3U2 (business_property) for",
                              "18500.00 USD is in review; no decision has been made",
                              "yet. No action is needed now. The decision can't be",
                              "predicted in chat.”"]):
        s.text(ex + 18, 762 + i * 24, line, 16)
    s.text(ex + 18, 880, "The model can't hint at the adjuster's notes:", 15.5, color=MUTED)
    s.text(ex + 18, 904, "they never reached it.", 15.5, color=MUTED)

    s.box(40, 990, 1120, 150, "grey")
    s.text(60, 1022, "Measured offline (scripted model, synthetic data)", 19, "bold")
    stats = [("12", "kinds of injected hallucination"), ("0", "reached the user, on two splits and two fresh seeds"),
             ("0", "false blocks on correct answers"),
             ("1", "repair, then code answers from the record, then a refusal")]
    for i, (n, label) in enumerate(stats):
        xx = 60 + i * 275
        s.text(xx, 1078, n, 36, "bold", C["code"][0])
        words, line, lines = label.split(), "", []
        for wd in words:
            if len(line) + len(wd) > 26:
                lines.append(line)
                line = wd
            else:
                line = (line + " " + wd).strip()
        lines.append(line)
        for j, ln in enumerate(lines):
            s.text(xx + (30 if n != "12" else 50), 1066 + j * 22, ln, 15.5, color=INK)
    s.text(60, 1124, "Live-traffic results on Bedrock are not measured yet.", 15, color=MUTED, italic=True)
    legend(s, 1196)
    s.save("finbot-gate.svg")


# ------------------------------------------------------------------ 3. a domain plug-in

def domain() -> None:
    s = Svg(1200, 1250)
    s.text(40, 54, "A domain plug-in: one module per product area", 32, "bold")
    s.text(40, 88, "The core never learns what a transfer is. Each area declares its record, its words and its "
                   "rules.", 18, color=MUTED)

    s.box(40, 120, 560, 560, "data")
    s.text(58, 154, "transfers.py  (money movement)", 21, "bold", C["data"][0])
    rows = [
        ("prefix · tool", "xfer_…  →  get_transfer(transfer_id)"),
        ("fields", "allow-list; hold_reason is not on it"),
        ("statuses", "completed · pending · returned · failed"),
        ("open statuses", "pending, processing: never “completed”"),
        ("policy", "R02 → don't resend; new details needed"),
        ("", "SETTLED_IRREVOCABLE → return request only"),
        ("", "COMPLIANCE_HOLD → can't share; don't resend"),
        ("causes", "R02 → account_closed → “account is closed”"),
        ("summary", "the record in one sentence, every field"),
        ("", "validated before it is repeated"),
    ]
    for i, (k, v) in enumerate(rows):
        yy = 198 + i * 46
        if k:
            s.text(58, yy, k, 16.5, "bold", INK)
        s.text(210, yy, v, 16, color=INK)
        if k and i:
            s.add(f'<line x1="58" y1="{yy - 30}" x2="582" y2="{yy - 30}" stroke="#ddd6fe"/>')
    s.text(58, 660, "One register(Domain(...)) call; no other file changes.", 15.5, color=MUTED, italic=True)

    s.box(640, 120, 520, 560, "code")
    s.text(658, 154, "What the core does with it, for every domain", 20, "bold", C["code"][0])
    core = [
        ("Field allow-list", "hidden fields never leave the tool"),
        ("Tenant scope", "another customer's record doesn't exist"),
        ("Provenance", "only ids the user typed are fetched"),
        ("Policy guidance", "the rule, worded for the reader's role"),
        ("Status check", "no undecided record called decided"),
        ("Cause check", "no reason the record doesn't carry"),
        ("Detail checks", "amounts, dates, rates, digits, links"),
        ("Resend check", "no “send it again” against policy"),
        ("Tickets", "cancel, unblock, appeal → a person"),
        ("Record answer", "the summary, if the model's fails"),
    ]
    for i, (k, v) in enumerate(core):
        yy = 198 + i * 46
        s.add(f'<circle cx="664" cy="{yy - 6}" r="5" fill="{C["code"][0]}"/>')
        s.text(678, yy, k, 16.5, "bold")
        s.text(838, yy, v, 15.5)
    s.arrow([(600, 400), (638, 400)], color=C["data"][0])

    s.text(40, 730, "Twelve domains, and the wrong answer each one is built to stop", 21, "bold")
    domains = [
        ("accounts", "acct_", "a guessed reason for a review"),
        ("transfers", "xfer_", "“cancel it” on a settled instant payment"),
        ("kyc", "kyc_", "“verified” while still in review"),
        ("lending", "loan_", "a reason that isn't on the record"),
        ("issuing", "card_", "a stolen card called active"),
        ("onboarding", "app_", "a promised decision date"),
        ("disputes", "dsp_", "a wrong respond-by date"),
        ("mortgages", "mtg_", "any talk of foreclosure"),
        ("bnpl", "bnpl_", "a wrong due date or late fee"),
        ("insurance", "clm_", "“you're covered”"),
        ("subscriptions", "sub_", "“cancelled” when it ends at period end"),
        ("payments", "txn_", "“retry now” on a hard decline"),
    ]
    for i, (name, pre, wrong) in enumerate(domains):
        col, row = i % 3, i // 3
        xx, yy = 40 + col * 377, 754 + row * 92
        s.box(xx, yy, 361, 80, "data" if name != "payments" else "grey", r=8, width=1.5)
        s.rich(xx + 16, yy + 30, [(name, "bold", C["data"][0] if name != "payments" else INK),
                                   ("   " + pre, "normal", MUTED)], 17)
        s.text(xx + 16, yy + 58, wrong, 15, color=INK)
    s.text(40, 1142, "Payments is one plug-in among twelve: leave it out of FINBOT_DOMAINS and its tools disappear.",
           15.5, color=MUTED)
    legend(s, 1190)
    s.save("finbot-domain.svg")


# ------------------------------------------------------------------ 4. platform security, edge to storage

def security() -> None:
    s = Svg(1200, 1530)
    s.text(40, 54, "finbot security: every layer assumes the one before it failed", 32, "bold")
    s.text(40, 88, "One request from the internet to the records and back. Left: the request path. "
                   "Right: the keys, grants and logs it relies on.", 18, color=MUTED)

    def stage(y, h, kind, title, lines):
        s.card(40, y, 680, h, kind, title, lines, title_size=20, line_size=16, gap=26)

    stage(120, 76, "people", "Customer, merchant or support agent", [])
    s.text(58, 182, "HTTPS with a bearer token from the identity provider", 15.5, color=MUTED)
    stage(226, 150, "block", "AWS WAF", ["per-IP rate limit · IP reputation · anonymous IPs",
                                         "AWS managed rules: known bad inputs, common, SQL injection",
                                         "body over 16 KB · token in a query string · unknown path",
                                         ])
    s.text(58, 362, "logs redact the Authorization header", 15, color=MUTED)
    stage(406, 124, "code", "Application Load Balancer", ["TLS 1.2+ with strong ciphers · HTTP redirected to HTTPS",
                                                           "drops invalid headers · strictest desync mitigation",
                                                           "access logs to an encrypted bucket"])
    stage(560, 124, "code", "HTTP API (WSGI)", ["JSON only, at most 16 KB, read with an exact length",
                                                "no query strings · strict id patterns · security headers",
                                                "fixed error messages: never why a token failed"])
    stage(714, 176, "code", "Service boundary, before the turn",
          ["verify token: signing-key ring by kid, then revocation list",
           "rate limit per user and per tenant (429 + Retry-After)",
           "tenant and role from the token, never from the chat text",
           "DLP on the request: card, SSN, IBAN, routing+account, keys masked"])
    stage(920, 150, "model", "Turn: model drafts, tools read",
          ["code mints a 5-minute delegation grant for this turn",
           "every tool call checks it: user, tenant, session, scope",
           "read-only ceiling: the agent can never be granted a write"])
    stage(1100, 124, "code", "Service boundary, after the turn",
          ["DLP on the response: same detectors, masked again",
           "card data found on the way out → egress_leak audit record",
           "only the category is logged, never the value"])
    for y0, y1 in [(196, 224), (376, 404), (530, 558), (684, 712), (890, 918), (1070, 1098)]:
        s.arrow([(380, y0), (380, y1)])

    # right column
    s.card(760, 714, 400, 176, "grey", "Token keys and revocation",
           ["OIDC (JWKS) or HS256 key ring", "rotate: previous key verifies until removed",
            "revoke one token (jti) or a whole user", "shared store, seen by all pods in 2 s"],
           title_size=19, line_size=15.5, gap=26)
    s.arrow([(758, 790), (722, 790)], color=C["code"][0])
    s.card(760, 920, 400, 150, "code", "Delegation grant (RFC 8693 style)",
           ["sub = the user · act = finbot-agent", "tenant · session · read:* scopes",
            "signed by code, never seen by the model", "denied call → refused, logged"],
           title_size=19, line_size=15.5, gap=24)
    s.arrow([(758, 995), (722, 995)], color=C["code"][0])

    s.card(760, 120, 400, 256, "data", "Encryption and keys (KMS)",
           ["AES-256-GCM envelope encryption", "data key per value, wrapped by KMS",
            "bound to tenant + session + purpose:", "  copied ciphertext won't decrypt",
            "key policy allows 4 purposes only",
            "yearly rotation · rewrap onto a new key", "required mode refuses plaintext"],
           title_size=19, line_size=15.5, gap=26)
    s.card(760, 406, 400, 278, "data", "Encrypted at rest",
           ["session state, user text, answers,", "turn metadata: sealed in the app",
            "DynamoDB: customer key + PITR", "SQS FIFO turn queue: KMS",
            "audit bucket: S3 Object Lock,", "  compliance mode, 1 year",
            "private subnets + VPC endpoints"],
           title_size=19, line_size=15.5, gap=26)
    s.arrow([(960, 376), (960, 404)], color=C["data"][0])

    s.card(760, 1100, 400, 124, "data", "Audit log",
           ["hash-chained per writer, HMAC-SHA256", "edit, delete or reorder → verify fails",
            "shipped to the Object Lock bucket"], title_size=19, line_size=15.5, gap=24)
    s.arrow([(722, 1162), (758, 1162)], color=C["data"][0])

    # supply chain
    s.box(40, 1258, 1120, 120, "grey")
    s.text(60, 1292, "Code and supply chain, on every push", 20, "bold", C["grey"][0])
    s.text(60, 1322, "CodeQL · Bandit · ruff security rules · mypy · pip-audit · gitleaks · Trivy image + config scan",
           16, color=INK)
    s.text(60, 1350, "Dependabot weekly · red team must stay at 0 successful attacks or the build fails",
           16, color=INK)

    s.text(40, 1416, "Red team, model that obeys anything: 0 of 34 attacks succeed defended, 25 of 34 undefended.",
           16.5, color=MUTED)
    legend(s, 1462)
    s.save("finbot-security.svg")


# ------------------------------------------------------------------ 5. the whole system

def system() -> None:
    s = Svg(1600, 1680)
    s.text(40, 54, "finbot: the whole system on one page", 32, "bold")
    s.text(40, 88, "Request path top to bottom; stores, ingestion and governance underneath. "
                   "Every box is Python in finbot/, infra/cdk or deploy/k8s.", 18, color=MUTED)
    L, G = 15.5, 24

    def card(x, y, w, h, kind, title, lines):
        s.card(x, y, w, h, kind, title, lines, title_size=19, line_size=L, gap=G)

    # row 1: edge
    card(40, 120, 300, 170, "people", "Clients", ["customers, support agents", "bearer token (OIDC)",
                                                   "client msg_id, reused on resend"])
    card(380, 120, 420, 170, "block", "Edge (infra/cdk)", ["WAF: per-IP rate, managed rules, 16 KB body",
                                                           "ALB: TLS 1.2+, HTTP to HTTPS",
                                                           "private subnets, VPC endpoints", "no NAT for AWS traffic"])
    card(840, 120, 720, 170, "code", "API boundary (http_api.py, service.py)",
         ["verify token (key ring by kid) and revocation (2 s) · deny-by-default roles",
          "rate limit per user and tenant, shared counters across pods",
          "tenant scope from the token · DLP in and out · cancel a running turn",
          "same 403 for another tenant's session and a missing one"])
    s.arrow([(340, 205), (378, 205)])
    s.arrow([(800, 205), (838, 205)])

    # row 2: queue and workers
    card(40, 330, 520, 150, "data", "Turn queue (SQS FIFO)", ["group = session, dedup = msg_id",
                                                             "retry later on outage · DLQ after 5",
                                                             "forged tenant in a message → DLQ"])
    card(600, 330, 960, 150, "code", "Worker pool (runtime.py, worker_main.py) · stateless, KEDA on queue depth",
         ["claim lease (60 s) → load committed state → journal turn by msg_id → run → CAS commit",
          "heartbeat before each model call · cancel flag checked there · late writer → StaleWrite",
          "crash = nothing committed; next worker re-runs from the last commit (tools are read-only)"])
    s.arrow([(1200, 290), (1200, 306), (300, 306), (300, 328)])
    s.text(320, 322, "FINBOT_DISPATCH=queue: enqueue, poll for the commit, 202 if slow", 14, color=MUTED,
           italic=True)
    s.arrow([(560, 405), (598, 405)])

    # row 3: the turn
    s.box(40, 520, 1520, 430, "grey", dash=True)
    s.text(60, 552, "One turn (orchestrator.py)", 21, "bold", C["grey"][0])
    s.arrow([(1080, 480), (1080, 518)])
    card(60, 570, 330, 220, "code", "Stages, in order", ["1 session risk lock", "2 input guard (injection)",
                                                        "3 language → person", "4 learned input rules",
                                                        "5 focus + user-typed ids", "6 write request → ticket",
                                                        "7 model loop, ≤ 6 tool rounds"])
    card(410, 570, 330, 220, "model", "Model engine (models.py)", ["Bedrock us-east-1", "→ Bedrock us-west-2",
                                                                    "→ Claude API", "retries inside 90 s deadline",
                                                                    "breaker per model, shared", "refusal → next model",
                                                                    "optional cheaper tier"])
    card(760, 570, 360, 220, "code", "Security gateway",
         ["record ids must come from the user", "arguments fit the tool schema",
          "rules: deny or need approval", "> 3 records in a turn → approval",
          "grant: scope, tenant, session, 5 min", "behaviour → session risk",
          "one security event stream"])
    card(1140, 570, 400, 220, "data", "Read-only tools over 12 domains",
         ["accounts · transfers · KYC · lending", "cards · onboarding · disputes",
          "mortgages · BNPL · insurance", "subscriptions · card payments",
          "field allow-list, tenant-scoped", "results screened + spotlighted",
          "help-page search (cached)"])
    card(60, 810, 720, 124, "code", "Hallucination gate (verification.py)",
         ["evidence · reason · domain status and cause · language · commitments · tickets",
          "value provenance: facts only from our own fields · contacts and links checked",
          "one repair → answer built from the record → \"I won't guess\" → person after 2"])
    card(800, 810, 740, 124, "code", "Output", ["output guard (contextual grounding) · PII mask · prompt-leak canary",
                                               "stale help page → note added by code",
                                               "DLP on the way out; card data found → egress_leak"])
    s.arrow([(390, 680), (408, 680)], color=C["model"][0])
    s.arrow([(740, 680), (758, 680)], color=C["code"][0])
    s.arrow([(1120, 680), (1138, 680)], color=C["code"][0])
    s.arrow([(575, 790), (575, 808)], color=C["model"][0])
    s.arrow([(780, 872), (798, 872)], color=C["code"][0])

    # row 4: stores and ingestion
    card(40, 990, 470, 230, "data", "Session store (DynamoDB · SQLite in dev)",
         ["S#<tenant>.<uuid>: state, journal, version, lease", "sealed AES-256-GCM, key per value (KMS)",
          "context: tenant + session + purpose", "per-tenant IAM role (LeadingKeys)",
          "H# breaker health · C# control · R# counters", "tickets and approvals · TTL · PITR"])
    card(530, 990, 300, 230, "data", "Cache (cache.py)", ["Valkey/Redis or memory", "help-page searches, 5 min",
                                                           "model health, 1 s", "never records or answers",
                                                           "outage = miss"])
    card(850, 990, 300, 230, "data", "Help-page index", ["OpenSearch · pgvector", "or local JSON",
                                                          "hybrid search", "access filter before rank",
                                                          "cites source_url"])
    card(1170, 990, 390, 230, "code", "Ingestion (ingestion/, nightly)", ["S3 · SharePoint · web · local",
                                                                          "screen injection → quarantine",
                                                                          "mask PII before chunking",
                                                                          "chunk → embed → upsert",
                                                                          "incremental by version, atomic save",
                                                                          "audit: stale or conflicting pages"])
    s.arrow([(1168, 1105), (1152, 1105)], color=C["code"][0])
    s.arrow([(800, 950), (800, 970), (275, 970), (275, 988)])
    s.arrow([(1340, 950), (1340, 970), (1000, 970), (1000, 988)])

    # row 5: observability and governance
    card(40, 1260, 370, 200, "code", "AI observability (telemetry.py)", ["OpenTelemetry GenAI spans:",
                                                                         "turn → chat → execute_tool → verify",
                                                                         "tokens, failover, reason codes",
                                                                         "no prompts, answers or ids"])
    card(430, 1260, 370, 200, "data", "Audit and security events", ["one line per turn and decision",
                                                                    "hash-chained, own KMS key",
                                                                    "S3 Object Lock, 1 year",
                                                                    "categories and counts, no values"])
    card(820, 1260, 360, 200, "code", "Control and adaptive rules", ["drain or restore a model",
                                                                     "rules from reviewed mistakes",
                                                                     "gate → shadow → canary → admin",
                                                                     "versioned, one-call rollback"])
    card(1200, 1260, 360, 200, "people", "People", ["ticket desk: write requests", "approve held tool calls",
                                                     "review queue, labels", "handoff after 2 failures"])

    s.text(40, 1508, "Verified offline (scripted model, synthetic data): 475 tests · 8/8 multi-process durability "
                     "scenarios (kill -9 mid-turn, frozen worker, lost reply,", 16, color=MUTED)
    s.text(40, 1532, "32-turn memory across restarts, model failover, 6-process contention, guardrail outage, "
                     "killed ingestion) · 0 of 2,259 injected hallucinations reach users ·", 16, color=MUTED)
    s.text(40, 1556, "0 of 34 injection attacks succeed defended · never deployed, never run against a live model.",
           16, color=MUTED)
    legend(s, 1610)
    s.save("finbot-system.svg")


if __name__ == "__main__":
    architecture()
    gate()
    domain()
    security()
    system()
    print("wrote", ", ".join(n for n in sorted(os.listdir(OUT)) if n.endswith(".svg")))
