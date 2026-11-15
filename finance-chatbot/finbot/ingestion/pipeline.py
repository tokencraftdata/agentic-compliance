"""Ingestion pipeline (template method): source -> screen -> chunk -> embed -> vector DB.

    for each document from the source:
      1. skip if its version is already indexed            (incremental, idempotent)
      2. screen: prompt-injection text -> QUARANTINE        (never indexed; human review)
                 card numbers / secrets  -> masked before embedding
      3. chunk (strategy; optionally Claude-contextualised)
      4. embed in batches (Bedrock; retries + breaker)
      5. upsert new chunk ids, THEN delete the doc's old ids (no window with zero chunks)
    full_sync=True: docs that vanished from the source are deleted from the index.

A failure on one document is recorded and the run continues; the report lists
indexed / skipped / quarantined / failed / deleted so an operator can act.
Model-written answers are never a source: the human review queue
(Chatbot.feedback) fixes the source docs, which then flow back through here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..guardrails import INJECTION
from ..knowledge import Chunk
from ..pii import mask_pii
from .vectorstores import PUBLIC


@dataclass
class IngestReport:
    indexed_docs: int = 0
    chunks: int = 0
    skipped_unchanged: int = 0
    quarantined: list = field(default_factory=list)   # (doc_id, reason)
    failed: list = field(default_factory=list)        # (doc_id, error)
    deleted_docs: int = 0
    masked_pii: int = 0
    seconds: float = 0.0


class IngestionPipeline:
    def __init__(self, source, chunker, embedder, store, batch_size: int = 32):
        self.source, self.chunker, self.embedder, self.store = source, chunker, embedder, store
        self.batch_size = batch_size

    def screen(self, doc: dict) -> str | None:
        """Reason to quarantine, or None. Retrieved text is untrusted input to the model."""
        from ..knowledge import _clean
        if INJECTION.search(_clean(doc["html"])):
            return "prompt_injection_pattern"
        return None

    def to_record(self, doc: dict, c: Chunk, vector: list[float]) -> dict:
        return {"id": c.id, "doc_id": doc["page_id"], "version": str(doc["version"]), "text": c.text,
                "context": c.context, "title": c.title, "section": c.section, "source_url": c.source_url,
                "updated": c.updated, "source": doc.get("source", ""), "access": c.access or [PUBLIC],
                "embed_model": self.embedder.name, "vector": vector}

    def run(self, full_sync: bool = False) -> IngestReport:
        t0, rep = time.time(), IngestReport()
        existing = self.store.doc_index()
        seen: set[str] = set()
        for doc in self.source.documents():
            doc_id = doc["page_id"]
            seen.add(doc_id)
            try:
                if existing.get(doc_id, {}).get("version") == str(doc["version"]):
                    rep.skipped_unchanged += 1
                    continue
                reason = self.screen(doc)
                if reason:
                    rep.quarantined.append((doc_id, reason))
                    if doc_id in existing:      # a previously clean doc turned bad: pull it
                        self.store.delete_ids(existing[doc_id]["ids"])
                    continue
                # Mask the whole document BEFORE chunking: a card number split across a chunk
                # boundary or an overlap would otherwise survive in both halves.
                doc = dict(doc)
                for key in ("html", "title"):
                    doc[key], found = mask_pii(str(doc.get(key, "")))
                    rep.masked_pii += len(found)
                chunks = self.chunker.chunk(doc)
                for c in chunks:   # Claude-written context could repeat a number: mask that too
                    c.context, found = mask_pii(c.context or "")
                    rep.masked_pii += len(found)
                records = []
                for i in range(0, len(chunks), self.batch_size):
                    batch = chunks[i:i + self.batch_size]
                    vecs = self.embedder.embed_documents(
                        [f"{c.title} > {c.section}\n{c.context}\n{c.text}".strip() for c in batch])
                    records += [self.to_record(doc, c, v) for c, v in zip(batch, vecs, strict=True)]
                self.store.upsert(records)
                stale = set(existing.get(doc_id, {}).get("ids", [])) - {r["id"] for r in records}
                if stale:
                    self.store.delete_ids(sorted(stale))
                rep.indexed_docs += 1
                rep.chunks += len(records)
            except Exception as e:  # noqa: BLE001 - one bad doc must not stop the sync
                rep.failed.append((doc_id, f"{type(e).__name__}: {e}"[:200]))
        if full_sync:
            for doc_id, info in existing.items():
                if doc_id not in seen:
                    self.store.delete_ids(info["ids"])
                    rep.deleted_docs += 1
        rep.seconds = round(time.time() - t0, 2)
        return rep
