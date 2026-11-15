"""Read side: KnowledgeSearch adapters the chatbot's search_knowledge_base tool calls.

    VectorKnowledge       any VectorStore + the SAME embedder used at ingestion.
                          Access filter inside the query, a minimum similarity, then a
                          lexical re-rank: dense vectors blur exact tokens (decline code
                          "51" vs "N7", "AVS_MISMATCH"), so candidates are re-scored by
                          the share of the query's content words they contain. A
                          coverage floor reports "least-bad chunk" as no match (the tool
                          returns not_found and the bot says it has no documentation).
    BedrockKnowledgeBase  fully managed: Bedrock Knowledge Bases chunk, embed and
                          write to their vector store (OpenSearch Serverless, Aurora
                          pgvector, Pinecone, ...) from native data sources (S3,
                          and the wiki/SaaS connectors of Bedrock Managed Knowledge Base).
                          sync() starts an ingestion job; search() calls Retrieve with
                          a metadata filter on access groups.

Both return the hit shape the evidence guard and FakeLLM already use:
chunk_id, title, section, text, source_url, version, updated, score.
`context` (Claude-written, retrieval only) is deliberately not returned.
"""
from __future__ import annotations

import time

from ..knowledge import content_terms


class VectorKnowledge:
    def __init__(self, store, embedder, min_score: float = 0.3, min_coverage: float = 0.5,
                 lexical_weight: float = 0.5, candidates: int = 25):
        self.store, self.embedder = store, embedder
        self.min_score, self.min_coverage = min_score, min_coverage
        self.lexical_weight, self.candidates = lexical_weight, candidates

    def search(self, query: str, k: int = 4, groups: list[str] | None = None, **_) -> list[dict]:
        q_terms = content_terms(query)
        ranked = []
        for score, r in self.store.query(self.embedder.embed_query(query), max(k, self.candidates), groups or []):
            if r.get("embed_model") and r["embed_model"] != self.embedder.name:
                raise RuntimeError(f"index built with {r['embed_model']}, querying with {self.embedder.name}")
            words = content_terms(f"{r['section']} {r['text']}")
            coverage = len(q_terms & words) / len(q_terms) if q_terms else 1.0
            if coverage < self.min_coverage:
                continue
            w = self.lexical_weight
            fused = (1 - w) * float(score) + w * coverage
            if fused >= self.min_score:  # threshold AFTER re-rank: an exact-code match can lift a weak vector
                ranked.append((fused, r))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [{"chunk_id": r["id"], "title": r["title"], "section": r["section"], "text": r["text"],
                 "source_url": r["source_url"], "version": r["version"], "updated": r["updated"],
                 "score": round(s, 3)} for s, r in ranked[:k]]


class BedrockKnowledgeBase:
    """Managed path. Put `access` (list of groups, "*" for public) in each source
    object's .metadata.json sidecar so Retrieve can filter on it."""

    def __init__(self, kb_id: str, region: str, min_score: float = 0.4):
        import boto3  # lazy
        self.rt = boto3.client("bedrock-agent-runtime", region_name=region)
        self.agent = boto3.client("bedrock-agent", region_name=region)
        self.kb_id, self.min_score = kb_id, min_score

    def search(self, query: str, k: int = 4, groups: list[str] | None = None, **_) -> list[dict]:
        flt = {"orAll": [{"listContains": {"key": "access", "value": g}} for g in ["*", *(groups or [])]]} \
            if groups else {"listContains": {"key": "access", "value": "*"}}
        r = self.rt.retrieve(knowledgeBaseId=self.kb_id, retrievalQuery={"text": query},
                             retrievalConfiguration={"vectorSearchConfiguration": {
                                 "numberOfResults": k, "overrideSearchType": "HYBRID", "filter": flt}})
        out = []
        for i, res in enumerate(r.get("retrievalResults", [])):
            if res.get("score", 0) < self.min_score:
                continue
            md, loc = res.get("metadata", {}), res.get("location", {})
            url = (loc.get("s3Location", {}).get("uri") or loc.get("confluenceLocation", {}).get("url")
                   or loc.get("sharePointLocation", {}).get("url") or loc.get("webLocation", {}).get("url") or "")
            out.append({"chunk_id": md.get("x-amz-bedrock-kb-chunk-id", f"bkb:{i}"), "title": md.get("title", ""),
                        "section": md.get("title", ""), "text": res["content"]["text"], "source_url": url,
                        "version": md.get("version", ""), "updated": md.get("updated", ""),
                        "score": round(res.get("score", 0), 3)})
        return out

    def sync(self, data_source_id: str, wait_s: float = 1800, poll_s: float = 15) -> dict:
        """Start an ingestion job (Bedrock chunks + embeds + writes vectors) and wait."""
        job = self.agent.start_ingestion_job(knowledgeBaseId=self.kb_id, dataSourceId=data_source_id)["ingestionJob"]
        deadline = time.time() + wait_s
        while job["status"] in ("STARTING", "IN_PROGRESS") and time.time() < deadline:
            time.sleep(poll_s)
            job = self.agent.get_ingestion_job(knowledgeBaseId=self.kb_id, dataSourceId=data_source_id,
                                               ingestionJobId=job["ingestionJobId"])["ingestionJob"]
        return {"status": job["status"], "stats": job.get("statistics", {}), "failures": job.get("failureReasons", [])}
