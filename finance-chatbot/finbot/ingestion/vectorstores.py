"""Vector stores (adapters behind one port). Ingestion ends here; the chatbot reads here.

    local       JSON file, brute-force cosine. Dev/tests and small corpora.
    opensearch  Amazon OpenSearch Serverless (vector search collection), k-NN with
                filter, SigV4 auth from the IAM role.
    pgvector    Postgres / Aurora with the pgvector extension (HNSW index).
    chroma      ChromaDB, a common self-hosted option.

Record = one chunk: id, doc_id, version, text, context, title, section, source_url,
updated, source, access, embed_model, vector.

Access control is stored WITH the vector and filtered INSIDE the query (local,
OpenSearch, pgvector), so a restricted chunk never reaches the model. Chroma
metadata cannot hold lists, so that adapter over-fetches and filters in code
before returning. Public chunks carry access ["*"].
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Protocol

PUBLIC = "*"


class VectorStore(Protocol):
    def upsert(self, records: list[dict]) -> None: ...

    def delete_ids(self, ids: list[str]) -> None: ...

    def doc_index(self) -> dict[str, dict]: ...   # doc_id -> {"version": v, "ids": [...]}

    def query(self, vector: list[float], k: int, groups: list[str]) -> list[tuple[float, dict]]: ...


def _allowed(rec_access: list[str], groups: list[str]) -> bool:
    return PUBLIC in rec_access or bool(set(rec_access) & set(groups or []))


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))


class LocalVectorStore:
    def __init__(self, path: str = ""):
        self.path = path
        self.records: dict[str, dict] = {}
        if path and os.path.exists(path):
            with open(path) as f:
                self.records = {r["id"]: r for r in json.load(f)}

    def upsert(self, records):
        for r in records:
            self.records[r["id"]] = r
        self._save()

    def delete_ids(self, ids):
        for i in ids:
            self.records.pop(i, None)
        self._save()

    def doc_index(self):
        out: dict[str, dict] = {}
        for r in self.records.values():
            d = out.setdefault(r["doc_id"], {"version": r["version"], "ids": []})
            d["ids"].append(r["id"])
        return out

    def query(self, vector, k, groups):
        for r in self.records.values():   # one check, before scoring: mismatched sizes give meaningless scores
            if len(r["vector"]) != len(vector):
                raise RuntimeError(f"index built with {len(r['vector'])}-dim vectors ({r.get('embed_model')}), "
                                   f"query vector has {len(vector)}")
            break
        scored = [(_cos(vector, r["vector"]), r) for r in self.records.values() if _allowed(r["access"], groups)]
        return sorted(scored, key=lambda x: x[0], reverse=True)[:k]

    def _save(self):
        if self.path:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(list(self.records.values()), f)
            os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written index


class OpenSearchVectorStore:
    """Amazon OpenSearch Serverless. Index mapping: see MAPPING (create once)."""

    def __init__(self, endpoint: str, index: str, region: str, dims: int):
        import boto3
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection  # lazy: pip install opensearch-py
        auth = AWSV4SignerAuth(boto3.Session().get_credentials(), region, "aoss")
        self.os = OpenSearch(hosts=[{"host": endpoint.replace("https://", ""), "port": 443}], http_auth=auth,
                             use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection, timeout=30)
        self.index, self.dims = index, dims

    def mapping(self) -> dict:
        return {"settings": {"index.knn": True},
                "mappings": {"properties": {
                    "vector": {"type": "knn_vector", "dimension": self.dims,
                               "method": {"name": "hnsw", "engine": "faiss", "space_type": "innerproduct"}},
                    "doc_id": {"type": "keyword"}, "version": {"type": "keyword"}, "access": {"type": "keyword"},
                    "text": {"type": "text"}, "context": {"type": "text"}, "source": {"type": "keyword"}}}}

    def upsert(self, records):
        from opensearchpy import helpers
        helpers.bulk(self.os, ({"_index": self.index, "_id": r["id"], "_source": r} for r in records))

    def delete_ids(self, ids):
        from opensearchpy import helpers
        helpers.bulk(self.os, ({"_op_type": "delete", "_index": self.index, "_id": i} for i in ids),
                     raise_on_error=False)

    def doc_index(self):
        out: dict[str, dict] = {}
        body = {"_source": ["doc_id", "version"], "query": {"match_all": {}}, "size": 1000}
        r = self.os.search(index=self.index, body=body, scroll="2m")
        while r["hits"]["hits"]:
            for h in r["hits"]["hits"]:
                d = out.setdefault(h["_source"]["doc_id"], {"version": h["_source"]["version"], "ids": []})
                d["ids"].append(h["_id"])
            r = self.os.scroll(scroll_id=r["_scroll_id"], scroll="2m")
        return out

    def query(self, vector, k, groups):
        body = {"size": k, "_source": {"excludes": ["vector"]}, "query": {"knn": {"vector": {
            "vector": vector, "k": k, "filter": {"terms": {"access": [PUBLIC, *(groups or [])]}}}}}}
        r = self.os.search(index=self.index, body=body)
        return [(h["_score"], h["_source"]) for h in r["hits"]["hits"]]


class PgVectorStore:
    """Postgres/Aurora + pgvector. DDL in self.ddl(); vectors normalised, so <#> = -inner product."""

    def __init__(self, dsn: str, table: str = "kb_chunks", dims: int = 1024):
        # The table name is spliced into SQL (identifiers can't be bound parameters), so it must be a
        # plain identifier; every value goes through bound parameters.
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table):
            raise ValueError(f"invalid table name {table!r}")
        import psycopg  # lazy: pip install "psycopg[binary]"
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.table, self.dims = table, int(dims)

    def ddl(self) -> str:
        return (f"CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE IF NOT EXISTS {self.table} ("
                f"id text PRIMARY KEY, doc_id text NOT NULL, version text NOT NULL, access text[] NOT NULL, "
                f"record jsonb NOT NULL, vector vector({self.dims}) NOT NULL); "
                f"CREATE INDEX IF NOT EXISTS {self.table}_hnsw ON {self.table} USING hnsw (vector vector_ip_ops); "
                f"CREATE INDEX IF NOT EXISTS {self.table}_doc ON {self.table} (doc_id);")

    def upsert(self, records):
        with self.conn.cursor() as cur:
            for r in records:
                rec = {k: v for k, v in r.items() if k != "vector"}
                cur.execute(f"INSERT INTO {self.table} VALUES (%s,%s,%s,%s,%s,%s::vector) ON CONFLICT (id) DO UPDATE "  # noqa: S608 - table name validated in __init__ # nosec B608
                            "SET doc_id=EXCLUDED.doc_id, version=EXCLUDED.version, access=EXCLUDED.access, "
                            "record=EXCLUDED.record, vector=EXCLUDED.vector",
                            (r["id"], r["doc_id"], str(r["version"]), r["access"], json.dumps(rec), str(r["vector"])))

    def delete_ids(self, ids):
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table} WHERE id = ANY(%s)", (ids,))  # noqa: S608 - table name validated in __init__ # nosec B608

    def doc_index(self):
        out: dict[str, dict] = {}
        with self.conn.cursor() as cur:
            for i, doc, ver in cur.execute(f"SELECT id, doc_id, version FROM {self.table}"):  # noqa: S608 - table name validated in __init__ # nosec B608
                out.setdefault(doc, {"version": ver, "ids": []})["ids"].append(i)
        return out

    def query(self, vector, k, groups):
        with self.conn.cursor() as cur:
            rows = cur.execute(f"SELECT record, -(vector <#> %s::vector) AS score FROM {self.table} "  # noqa: S608 - table name validated in __init__ # nosec B608
                               "WHERE access && %s ORDER BY vector <#> %s::vector LIMIT %s",
                               (str(vector), [PUBLIC, *(groups or [])], str(vector), k)).fetchall()
        return [(float(s), rec) for rec, s in rows]


class ChromaVectorStore:
    def __init__(self, path: str, collection: str = "finbot_kb"):
        import chromadb  # lazy
        self.col = chromadb.PersistentClient(path=path).get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"})

    def upsert(self, records):
        # Chroma metadata can't hold lists: access is stored as ",grp1,grp2," for $contains-free exact checks.
        self.col.upsert(ids=[r["id"] for r in records], embeddings=[r["vector"] for r in records],
                        documents=[r["text"] for r in records],
                        metadatas=[{**{k: v for k, v in r.items() if k not in ("vector", "text", "access")},
                                    "version": str(r["version"]), "access": "," + ",".join(r["access"]) + ","}
                                   for r in records])

    def delete_ids(self, ids):
        self.col.delete(ids=ids)

    def doc_index(self):
        out: dict[str, dict] = {}
        got = self.col.get(include=["metadatas"])
        for i, m in zip(got["ids"], got["metadatas"], strict=True):
            out.setdefault(m["doc_id"], {"version": m["version"], "ids": []})["ids"].append(i)
        return out

    def query(self, vector, k, groups):
        r = self.col.query(query_embeddings=[vector], n_results=max(k * 4, 20), include=["metadatas", "documents",
                                                                                         "distances"])
        out = []
        for m, doc, dist in zip(r["metadatas"][0], r["documents"][0], r["distances"][0], strict=True):
            access = [a for a in m["access"].split(",") if a]
            if _allowed(access, groups):   # post-filter: over-fetch above, then trim
                out.append((1 - dist, {**m, "text": doc, "access": access}))
        return out[:k]
