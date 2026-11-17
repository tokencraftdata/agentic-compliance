"""Knowledge base: structure-aware chunking and hybrid retrieval.

Design choices (each avoids a common RAG shortcut):
  * chunks follow document structure (headings) with overlap, so a chunk is a
    self-contained answer unit and carries its section path as context;
  * every chunk keeps provenance (page id, version, url, updated time) so answers
    can cite sources and stale pages can be re-ingested by version;
  * retrieval is hybrid: BM25 (exact terms like error codes "05", "AVS_MISMATCH")
    plus optional dense embeddings (Bedrock Titan) fused with reciprocal rank;
  * a minimum-score threshold means "no good match" is reported as such instead
    of feeding the LLM the least-bad chunk.

The index is a JSON file for portability. Swap `Index` for OpenSearch / pgvector /
Bedrock Knowledge Bases in production; the interface is search(query, k, filters).
"""
from __future__ import annotations

import html
import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .pii import mask_pii

TOKEN = re.compile(r"[a-z0-9_]+")
HEADING = re.compile(r"<h([1-4])[^>]*>(.*?)</h\1>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")


STOP = frozenset("""
    a an the is are was were be of to in on for and or but what how why does do did can i my me you your it
    this that with about explain tell please mean means work works look like general generally kind sort any
    some show give long take takes taking much often usually
""".split())


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def content_terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in STOP}


@dataclass
class Chunk:
    id: str
    text: str
    title: str
    section: str
    source_url: str
    page_id: str
    version: int
    updated: str
    space: str = ""
    access: list[str] = field(default_factory=list)  # groups allowed to see it
    embedding: list[float] | None = None
    # Retrieval-only context (e.g. written by Claude at ingestion). Used for embedding
    # and ranking, NEVER returned as evidence: grounding must be the source text itself.
    context: str = ""
    source: str = ""                                  # connector that produced it (local, s3, ...)


# ---------------------------------------------------------------- ingestion

def _clean(fragment: str) -> str:
    text = re.sub(r"<(br|/p|/li|/tr)[^>]*>", "\n", fragment, flags=re.I)
    text = html.unescape(TAG.sub(" ", text))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def chunk_page(page: dict, max_words: int = 220, overlap: int = 40) -> list[Chunk]:
    """Split by headings first, then by word windows with overlap inside a section."""
    body = page["html"]
    sections: list[tuple[str, str]] = []
    path: list[tuple[int, str]] = []   # (level, heading); a heading closes deeper and same-level ones
    last = 0
    current = page["title"]
    for m in HEADING.finditer(body):
        sections.append((current, body[last:m.start()]))
        level, heading = int(m.group(1)), _clean(m.group(2))
        path = [(lv, h) for lv, h in path if lv < level] + [(level, heading)]
        current = " > ".join([page["title"], *(h for _, h in path)])
        last = m.end()
    sections.append((current, body[last:]))

    chunks: list[Chunk] = []
    for section, raw in sections:
        words = _clean(raw).split()
        if not words:
            continue
        step = max(1, max_words - overlap)
        for i in range(0, len(words), step):
            window = words[i : i + max_words]
            chunks.append(Chunk(
                id=f"{page['page_id']}:v{page['version']}:{len(chunks)}",
                text=" ".join(window), title=page["title"], section=section,
                source_url=page["url"], page_id=page["page_id"], version=page["version"],
                updated=page["updated"], space=page.get("space", ""),
                access=page.get("access", []),
            ))
            if i + max_words >= len(words):
                break
    return chunks


# ---------------------------------------------------------------- embeddings

def titan_embedder(region: str, model_id: str) -> Callable[[str], list[float]]:
    import boto3  # lazy: optional dependency

    client = boto3.client("bedrock-runtime", region_name=region)

    def embed(text: str) -> list[float]:
        resp = client.invoke_model(modelId=model_id, body=json.dumps(
            {"inputText": text[:8000], "dimensions": 512, "normalize": True}))
        return json.loads(resp["body"].read())["embedding"]

    return embed


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------- index

class Index:
    def __init__(self, chunks: list[Chunk] | None = None, embed: Callable[[str], list[float]] | None = None):
        self.embed = embed
        self.generation = 0
        self._reset(chunks or [])

    def _reset(self, chunks: list[Chunk]) -> None:
        """Rebuild every index structure from `chunks`."""
        self.chunks: list[Chunk] = []
        self._df: Counter = Counter()
        self._tf: list[Counter] = []
        self._len: list[int] = []
        for c in chunks:
            self.add(c)

    def upsert_page(self, page: dict) -> int:
        """Replace all chunks of a page (idempotent re-ingest on new versions)."""
        keep = [c for c in self.chunks if c.page_id != page["page_id"]]
        page = {**page, "html": mask_pii(page.get("html", ""))[0], "title": mask_pii(page.get("title", ""))[0]}
        new = chunk_page(page)
        if self.embed:
            for c in new:
                c.embedding = self.embed(f"{c.section}\n{c.text}")
        self._reset(keep)
        for c in new:
            self.add(c)
        return len(new)

    def add(self, c: Chunk) -> None:
        self.generation = getattr(self, "generation", 0) + 1   # any change invalidates cached searches
        toks = tokenize(f"{c.section} {c.text}")
        tf = Counter(toks)
        self.chunks.append(c)
        self._tf.append(tf)
        self._len.append(len(toks))
        self._df.update(tf.keys())

    def _bm25(self, query: str, k1: float = 1.4, b: float = 0.75) -> list[float]:
        n = len(self.chunks) or 1
        avg = (sum(self._len) / n) or 1.0
        q = tokenize(query)
        scores = []
        for tf, ln in zip(self._tf, self._len, strict=True):
            s = 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                    s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * ln / avg))
            scores.append(s)
        return scores

    def search(self, query: str, k: int = 4, groups: list[str] | None = None,
               min_bm25: float = 1.0, min_coverage: float = 0.5) -> list[dict]:
        """Hybrid search with access filtering BEFORE ranking.

        min_coverage: share of the query's content words a chunk must contain.
        Stops one incidental word ("standard") from counting as a match.
        """
        q_terms = content_terms(query)
        allowed = [i for i, c in enumerate(self.chunks)
                   if not c.access or (groups and set(c.access) & set(groups))]
        if not allowed:
            return []
        bm = self._bm25(query)
        lex = sorted(allowed, key=lambda i: bm[i], reverse=True)
        rankings = [lex]
        if self.embed and all(self.chunks[i].embedding for i in allowed):
            qv = self.embed(query)
            dense = sorted(allowed, key=lambda i: _cos(qv, self.chunks[i].embedding or []), reverse=True)
            rankings.append(dense)
        fused: dict[int, float] = {}
        for ranking in rankings:  # reciprocal rank fusion
            for r, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + r)
        out = []
        for i, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True):
            if bm[i] < min_bm25 and len(rankings) == 1:
                continue  # lexical-only mode: drop non-matches instead of padding
            if q_terms and len(q_terms & set(self._tf[i])) / len(q_terms) < min_coverage:
                continue  # too few of the query's content words: not an answer
            c = self.chunks[i]
            out.append({"chunk_id": c.id, "title": c.title, "section": c.section,
                        "text": c.text, "source_url": c.source_url,
                        "version": c.version, "updated": c.updated,
                        "score": round(bm[i], 3)})
            if len(out) == k:
                break
        return out

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump([asdict(c) for c in self.chunks], f)

    @classmethod
    def load(cls, path: str, embed=None) -> Index:
        with open(path) as f:
            return cls([Chunk(**d) for d in json.load(f)], embed)
