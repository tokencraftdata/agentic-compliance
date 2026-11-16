"""Embedders (strategy): documents and queries may be embedded differently.

    BedrockEmbedder   Amazon Bedrock invoke_model:
                        amazon.titan-embed-text-v2:0   one text per call, 256/512/1024 dims
                        cohere.embed-*                 batches of 96, input_type document/query
                      retries + circuit breaker; IAM role credentials.
    HashingEmbedder   deterministic, offline (tests, local demos). Not semantic.

The SAME embedder (model + dims) must be used at ingestion and query time; the
vector store records `embed_model` and the retriever refuses a mismatch.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from ..resilience import CircuitBreaker, retry_call


class BedrockEmbedder:
    def __init__(self, model_id: str = "amazon.titan-embed-text-v2:0", region: str = "us-east-1", dims: int = 1024):
        import boto3  # lazy
        from botocore.config import Config
        self.client = boto3.client("bedrock-runtime", region_name=region,
                                   config=Config(retries={"mode": "adaptive", "max_attempts": 4}))
        self.model_id, self.dims = model_id, dims
        self.name = f"bedrock:{model_id}:{dims}"
        self.breaker = CircuitBreaker(f"embed:{model_id}")

    def _invoke(self, body: dict) -> dict:
        def call():
            r = self.client.invoke_model(modelId=self.model_id, body=json.dumps(body))
            return json.loads(r["body"].read())
        return retry_call(call, attempts=4, base_s=0.5, breaker=self.breaker)

    def _embed(self, texts: list[str], kind: str) -> list[list[float]]:
        if self.model_id.startswith("cohere."):
            out = []
            for i in range(0, len(texts), 96):
                r = self._invoke({"texts": [t[:2048] for t in texts[i:i + 96]],
                                  "input_type": "search_document" if kind == "doc" else "search_query",
                                  "embedding_types": ["float"]})
                emb = r["embeddings"]
                out += emb["float"] if isinstance(emb, dict) else emb
            return out
        return [self._invoke({"inputText": t[:8000], "dimensions": self.dims, "normalize": True})["embedding"]
                for t in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "doc")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


class HashingEmbedder:
    name = "hashing:256"

    def __init__(self, dims: int = 256):
        self.dims = dims
        self.name = f"hashing:{dims}"

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for tok in re.findall(r"[a-z0-9_]+", text.lower()):
            h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)
            v[h % self.dims] += 1.0 if (h >> 8) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._one(t) for t in texts]

    def embed_query(self, text):
        return self._one(text)
