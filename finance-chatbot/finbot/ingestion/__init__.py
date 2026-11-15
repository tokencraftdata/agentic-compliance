"""Ingestion (Python data plane): many sources -> chunk -> Bedrock embed -> vector DB.

Two paths, same read interface for the chatbot (KnowledgeSearch):

  self-managed   IngestionPipeline(source, chunker, embedder, vector store)
                 you control chunking, screening, PII masking, Claude context
  managed        BedrockKnowledgeBase.sync(): Bedrock chunks, embeds and writes
                 vectors from its native connectors; you control less, run less

Factories (spec strings from config / CLI):
  SOURCES        local:<path> | s3:<bucket>/<prefix> |
                 sharepoint:<site_id>/<drive_id> | web:<url>[,<url>...]
  VECTOR_STORES  local:<file.json> | opensearch:<endpoint>/<index> | pgvector:<dsn> | chroma:<path>
  INGEST_EMBEDDERS bedrock:<model_id>[@dims] | hashing[:dims]
"""
from __future__ import annotations

from ..registry import Registry
from .chunkers import ClaudeContextualizer, HeadingChunker
from .embedders import BedrockEmbedder, HashingEmbedder
from .pipeline import IngestionPipeline, IngestReport
from .retrieval import BedrockKnowledgeBase, VectorKnowledge
from .sources import LocalSource, S3Source, SharePointSource, WebSource
from .vectorstores import ChromaVectorStore, LocalVectorStore, OpenSearchVectorStore, PgVectorStore

SOURCES: Registry = Registry("ingestion source")
VECTOR_STORES: Registry = Registry("vector store")
INGEST_EMBEDDERS: Registry = Registry("embedder")


def _pair(arg: str, sep: str, what: str, right_optional: bool = False, last: bool = False) -> tuple[str, str]:
    """Split "left<sep>right" with a clear error, instead of a TypeError from argument unpacking.
    last=True splits at the last separator (an endpoint URL contains slashes of its own)."""
    left, found, right = arg.rpartition(sep) if last else arg.partition(sep)
    if right_optional and (not found or (left and not right)):
        return (left if found else arg), ""
    if not found or not left or not right:
        raise ValueError(f"expected {what}, got {arg!r}")
    return left, right


def _split(spec: str) -> tuple[str, str]:
    kind, _, arg = spec.partition(":")
    return kind, arg


SOURCES.register("local")(lambda arg, access=None, **_: LocalSource(arg, access))
SOURCES.register("s3")(lambda arg, access=None, region="", **_: S3Source(
    *_pair(arg, "/", "bucket[/prefix]", right_optional=True), region=region, access=access))
SOURCES.register("sharepoint")(lambda arg, access=None, **_: SharePointSource(
    *_pair(arg, "/", "site_id/drive_id"), access=access))
SOURCES.register("web")(lambda arg, access=None, **_: WebSource(arg.split(","), access=access))

VECTOR_STORES.register("local")(lambda arg, **_: LocalVectorStore(arg))
VECTOR_STORES.register("opensearch")(lambda arg, region="us-east-1", dims=1024, **_:
                                     OpenSearchVectorStore(*_pair(arg, "/", "endpoint/index", last=True), region=region,
                                                           dims=dims))
VECTOR_STORES.register("pgvector")(lambda arg, dims=1024, **_: PgVectorStore(arg, dims=dims))
VECTOR_STORES.register("chroma")(lambda arg, **_: ChromaVectorStore(arg))


@INGEST_EMBEDDERS.register("bedrock")
def _bedrock(arg, region="us-east-1", **_):
    model, _, dims = arg.partition("@")          # bedrock:amazon.titan-embed-text-v2:0@1024
    return BedrockEmbedder(model or "amazon.titan-embed-text-v2:0", region, int(dims or 1024))


INGEST_EMBEDDERS.register("hashing")(lambda arg, **_: HashingEmbedder(int(arg) if arg else 256))


def source_from(spec: str, **kw):
    kind, arg = _split(spec)
    return SOURCES.create(kind, arg=arg, **kw)


def store_from(spec: str, **kw):
    kind, arg = _split(spec)
    return VECTOR_STORES.create(kind, arg=arg, **kw)


def embedder_from(spec: str, **kw):
    kind, arg = _split(spec)
    return INGEST_EMBEDDERS.create(kind, arg=arg, **kw)


__all__ = ["IngestionPipeline", "IngestReport", "HeadingChunker", "ClaudeContextualizer", "BedrockEmbedder",
           "HashingEmbedder", "VectorKnowledge", "BedrockKnowledgeBase", "LocalSource",
           "S3Source", "SharePointSource", "WebSource", "LocalVectorStore", "OpenSearchVectorStore",
           "PgVectorStore", "ChromaVectorStore", "SOURCES", "VECTOR_STORES", "INGEST_EMBEDDERS",
           "source_from", "store_from", "embedder_from"]
