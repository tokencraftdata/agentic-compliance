"""Ingestion sources (adapters): every connector yields the same Document shape.

    local       a folder of .md / .txt / .html files, or a kb_pages.json export
    s3          an S3 prefix (.md/.txt/.html/.json); ETag = version;
                object metadata `access-groups` = who may see it
    sharepoint  a SharePoint document library via Microsoft Graph (app-only token)
    web         an allow-listed public site (same-domain crawl, robots.txt honoured)

Document is the page dict the chunker already understands:
    page_id, title, url, html, version, updated, space, access, source

Credentials come from env vars / IAM roles, never from the source spec string.
Wiki tools with their own APIs are reached through Bedrock Knowledge Bases'
managed connectors (see retrieval.BedrockKnowledgeBase) rather than custom code here.
Each network call goes through retry_call (backoff + breaker), so one flaky
page does not abort a 10,000-page sync.
"""
from __future__ import annotations

import hashlib
import html as htmlmod
import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from functools import partial
from typing import Protocol

from ..resilience import CircuitBreaker, TransientError, retry_call

Document = dict


class Source(Protocol):
    name: str

    def documents(self) -> Iterable[Document]: ...


def _version(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:12]


_MD_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https URL. urllib otherwise follows http and ftp
    redirects, so a source page could steer ingestion to plain text or another protocol."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects)


def _require_https(url: str) -> None:
    """Sources are fetched over https only: no file://, ftp:// or plain http (SSRF and
    local-file reads through a crafted link or config value)."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError(f"refusing to fetch non-https URL: {parts.scheme or '?'}://{parts.netloc}")


def _open(req: urllib.request.Request, timeout: float):
    _require_https(req.full_url)
    return _OPENER.open(req, timeout=timeout)   # scheme checked above and on every redirect


def _fetch(req: urllib.request.Request, timeout: float) -> str:
    with _open(req, timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def text_to_html(text: str, markdown: bool = True) -> str:
    """Plain text / Markdown -> the minimal HTML the heading-aware chunker reads."""
    out: list[str] = []
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + htmlmod.escape(" ".join(para)) + "</p>")
            para.clear()
    for line in text.splitlines():
        m = _MD_HEADING.match(line) if markdown else None
        if m:
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{htmlmod.escape(m.group(2).strip())}</h{n}>")
        elif line.strip():
            para.append(line.strip())
        else:
            flush()
    flush()
    return "".join(out)


def make_doc(source: str, doc_id: str, title: str, url: str, body: str, *, is_html: bool, version=None,
             updated: str = "", space: str = "", access: list[str] | None = None) -> Document:
    html_body = body if is_html else text_to_html(body)
    return {"page_id": f"{source}:{doc_id}", "title": title, "url": url, "html": html_body,
            "version": version if version is not None else _version(html_body), "updated": updated,
            "space": space, "access": list(access or []), "source": source}


def _http_json(url: str, headers: dict, breaker: CircuitBreaker, data: bytes | None = None) -> dict:
    def call():
        req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - https enforced in _open
        try:
            with _open(req, 30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"HTTP {e.code}") from e
            raise
    return retry_call(call, attempts=4, base_s=1, breaker=breaker)


# ------------------------------------------------------------------ local

class LocalSource:
    name = "local"

    def __init__(self, path: str, access: list[str] | None = None, base_url: str = "file://"):
        self.path, self.access, self.base_url = path, access or [], base_url

    def documents(self) -> Iterator[Document]:
        if os.path.isfile(self.path) and self.path.endswith(".json"):   # kb_pages.json export
            with open(self.path) as f:
                for p in json.load(f):
                    yield {**p, "page_id": str(p["page_id"]), "access": p.get("access") or self.access,
                           "source": p.get("source", "local")}
            return
        for root, _dirs, files in os.walk(self.path):
            for fn in sorted(files):
                ext = os.path.splitext(fn)[1].lower()
                if ext not in (".md", ".txt", ".html", ".htm"):
                    continue
                full = os.path.join(root, fn)
                with open(full, encoding="utf-8", errors="replace") as f:
                    body = f.read()
                rel = os.path.relpath(full, self.path)
                yield make_doc("local", rel, os.path.splitext(fn)[0].replace("_", " "), self.base_url + rel,
                               body, is_html=ext in (".html", ".htm"), access=self.access)


# ------------------------------------------------------------------ s3

class S3Source:
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "", region: str = "", access: list[str] | None = None):
        import boto3  # lazy
        self.s3 = boto3.client("s3", region_name=region or None)
        self.bucket, self.prefix, self.access = bucket, prefix, access or []

    def documents(self) -> Iterator[Document]:
        pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix)
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                ext = os.path.splitext(key)[1].lower()
                if ext not in (".md", ".txt", ".html", ".htm", ".json"):
                    continue
                r = self.s3.get_object(Bucket=self.bucket, Key=key)
                body = r["Body"].read().decode("utf-8", errors="replace")
                groups = [g for g in r.get("Metadata", {}).get("access-groups", "").split(",") if g] or self.access
                if ext == ".json":   # one exported page per object
                    p = json.loads(body)
                    yield {**p, "page_id": f"s3:{key}", "version": obj["ETag"].strip('"'), "access": groups,
                           "source": "s3"}
                    continue
                yield make_doc("s3", key, os.path.basename(key), f"s3://{self.bucket}/{key}", body,
                               is_html=ext in (".html", ".htm"), version=obj["ETag"].strip('"'),
                               updated=obj["LastModified"].isoformat(), access=groups)


# ------------------------------------------------------------------ sharepoint

class SharePointSource:
    """Microsoft Graph, app-only (client credentials). Needs Sites.Read.All or a
    Sites.Selected grant on this one site: least privilege."""
    name = "sharepoint"
    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self, site_id: str, drive_id: str, access: list[str] | None = None):
        self.site_id, self.drive_id, self.access = site_id, drive_id, access or []
        self.breaker = CircuitBreaker("source:sharepoint")

    def _token(self) -> str:
        tenant = os.environ["SHAREPOINT_TENANT_ID"]
        data = urllib.parse.urlencode({
            "client_id": os.environ["SHAREPOINT_CLIENT_ID"], "client_secret": os.environ["SHAREPOINT_CLIENT_SECRET"],
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()
        return _http_json(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                          {"Content-Type": "application/x-www-form-urlencoded"}, self.breaker, data)["access_token"]

    def documents(self) -> Iterator[Document]:
        h = {"Authorization": f"Bearer {self._token()}"}
        stack = [f"{self.GRAPH}/drives/{self.drive_id}/root/children"]
        while stack:
            url: str | None = stack.pop()
            while url:
                page = _http_json(url, h, self.breaker)
                for item in page.get("value", []):
                    if "folder" in item:
                        stack.append(f"{self.GRAPH}/drives/{self.drive_id}/items/{item['id']}/children")
                        continue
                    ext = os.path.splitext(item["name"])[1].lower()
                    if ext not in (".md", ".txt", ".html", ".htm"):
                        continue  # .docx/.pdf: add a converter (python-docx / Textract) behind this check
                    req = urllib.request.Request(f"{self.GRAPH}/drives/{self.drive_id}/items/{item['id']}/content",  # noqa: S310 - https enforced in _open
                                                 headers=h)
                    body = retry_call(partial(_fetch, req, 60), attempts=3, breaker=self.breaker)
                    yield make_doc("sharepoint", item["id"], item["name"], item.get("webUrl", ""), body,
                                   is_html=ext in (".html", ".htm"), version=item.get("eTag"),
                                   updated=item.get("lastModifiedDateTime", ""), access=self.access)
                url = page.get("@odata.nextLink")


# ------------------------------------------------------------------ web

class WebSource:
    """Same-domain crawl from start URLs, allow-listed hosts only, robots.txt honoured."""
    name = "web"

    def __init__(self, start_urls: list[str], max_pages: int = 200, access: list[str] | None = None):
        self.start, self.max_pages, self.access = start_urls, max_pages, access or []
        self.hosts = {urllib.parse.urlparse(u).netloc for u in start_urls}
        self.breaker = CircuitBreaker("source:web")

    def documents(self) -> Iterator[Document]:
        import urllib.robotparser
        robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        seen: set[str] = set()
        queue = list(self.start)
        while queue and len(seen) < self.max_pages:
            url = urllib.parse.urldefrag(queue.pop(0))[0]
            host = urllib.parse.urlparse(url).netloc
            if url in seen or host not in self.hosts:
                continue
            if host not in robots:
                rp = urllib.robotparser.RobotFileParser(f"https://{host}/robots.txt")
                try:
                    rp.read()
                except Exception:  # noqa: BLE001 - unreadable robots.txt: be conservative
                    rp.parse(["User-agent: *", "Disallow: /"])
                robots[host] = rp
            if not robots[host].can_fetch("finbot-ingest", url):
                continue
            seen.add(url)
            req = urllib.request.Request(url, headers={"User-Agent": "finbot-ingest"})  # noqa: S310 - https enforced in _open
            try:
                body = retry_call(partial(_fetch, req, 30), attempts=3, breaker=self.breaker)
            except Exception:  # noqa: BLE001, S112 - skip one bad page, keep crawling
                continue
            title = re.search(r"<title>(.*?)</title>", body, re.I | re.S)
            main = re.search(r"<(main|article)[^>]*>(.*?)</\1>", body, re.I | re.S)
            content = re.sub(r"<(script|style|nav|footer)[^>]*>.*?</\1>", "", main.group(2) if main else body,
                             flags=re.I | re.S)
            yield make_doc("web", url, htmlmod.unescape(title.group(1).strip()) if title else url, url, content,
                           is_html=True, access=self.access)
            for href in re.findall(r'href="([^"#]+)"', body):
                queue.append(urllib.parse.urljoin(url, href))
