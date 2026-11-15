"""Ingest any source into any vector store (re-runs are incremental by document version).

  # local folder -> local JSON vectors, offline embedder (demo)
  python scripts/ingest.py --source local:data/kb_pages.json --store local:kb_vectors.json --embed hashing

  # S3 -> OpenSearch Serverless, Titan v2 embeddings, Claude context per chunk
  python scripts/ingest.py \
      --source s3:my-bucket/policies/ --store opensearch:https://abc.us-east-1.aoss.amazonaws.com/finbot-kb \
      --embed bedrock:amazon.titan-embed-text-v2:0@1024 --contextualize bedrock:anthropic.claude-haiku-5-5

  # SharePoint / web
  --source sharepoint:<site_id>/<drive_id>   --source web:https://docs.example.com/

  # Managed: Bedrock Knowledge Base does chunk + embed + write itself. Use this for wiki
  # tools (its managed connectors handle auth, crawling and incremental sync)
  python scripts/ingest.py --bedrock-kb KBID --data-source DSID
"""
import argparse
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.config import Settings  # noqa: E402
from finbot.ingestion import (  # noqa: E402
    BedrockKnowledgeBase,
    ClaudeContextualizer,
    HeadingChunker,
    IngestionPipeline,
    embedder_from,
    source_from,
    store_from,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", action="append", default=[], help="repeatable source spec")
    p.add_argument("--store", default="local:kb_vectors.json")
    p.add_argument("--embed", default="bedrock:amazon.titan-embed-text-v2:0@1024")
    p.add_argument("--groups", default="", help="access groups for sources without their own ACLs")
    p.add_argument("--contextualize", default="", help="provider:model for Claude chunk context, e.g. "
                                                       "bedrock:anthropic.claude-haiku-5-5")
    p.add_argument("--full-sync", action="store_true", help="delete docs that vanished from the source")
    p.add_argument("--bedrock-kb", default="")
    p.add_argument("--data-source", default="")
    a = p.parse_args()
    s = Settings()

    if a.bedrock_kb:
        print(json.dumps(BedrockKnowledgeBase(a.bedrock_kb, s.aws_region).sync(a.data_source), indent=2, default=str))
        return

    embedder = embedder_from(a.embed, region=s.aws_region)
    store = store_from(a.store, region=s.aws_region, dims=getattr(embedder, "dims", 1024))
    chunker = HeadingChunker()
    if a.contextualize:
        from finbot.llm import make_client
        provider, _, model = a.contextualize.partition(":")
        chunker = ClaudeContextualizer(chunker, make_client(provider, model, s.aws_region, effort="low"))
    groups = [g for g in a.groups.split(",") if g]
    for spec in a.source or [f"local:{os.path.join(s.data_dir, 'kb_pages.json')}"]:
        rep = IngestionPipeline(source_from(spec, access=groups, region=s.aws_region), chunker, embedder,
                                store).run(full_sync=a.full_sync)
        print(spec, json.dumps(asdict(rep), indent=2))


if __name__ == "__main__":
    main()
