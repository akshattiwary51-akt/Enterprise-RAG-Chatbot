"""Vector store adapters.

Security contract shared by every adapter:
  * `query()` REQUIRES an explicit list of permitted document IDs and filters on it
    *inside the vector store*. There is no unfiltered search method.
  * An empty permitted list returns no hits (Chroma raises on an empty `$in`, which we
    verified against chromadb 1.5.x -- we short-circuit instead, failing closed).
  * Chunk metadata carries only `document_id`, `index_version`, `parent_id`, locator
    fields and ordinal. Authoritative ACLs live in the relational database; the vector
    store holds no permission data that could go stale.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from ...core.errors import ApiError


class VectorStoreUnavailable(RuntimeError):
    """Vector database is down or inconsistent. Callers must fail closed."""


class EmbeddingConfigMismatch(RuntimeError):
    """The index was built with a different embedding model than the one configured."""


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    index_version: int
    parent_id: str
    ordinal: int
    text: str
    locator: dict = field(default_factory=dict)


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    index_version: int
    parent_id: str
    score: float  # cosine similarity in [-1, 1]; higher is better
    locator: dict


class VectorStore(Protocol):
    def ping(self) -> None: ...
    def add_chunks(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None: ...
    def delete_document(self, document_id: str) -> None: ...
    def count_document_chunks(self, document_id: str) -> int: ...
    def query(self, embedding: list[float], document_ids: list[str], top_k: int) -> list[Hit]: ...


def _flatten_locator(locator: dict) -> str:
    return json.dumps(locator, sort_keys=True, separators=(",", ":"))


class ChromaVectorStore:
    """Persistent ChromaDB collection (cosine space). Embedding model name is pinned in the
    collection metadata and verified on every open so ingestion and query can never drift."""

    MAX_IN_BATCH = 500  # permitted-ID list is chunked to keep filters a sane size

    def __init__(self, path: str, collection: str, embedding_model: str) -> None:
        import chromadb

        self._embedding_model = embedding_model
        try:
            self._client = chromadb.PersistentClient(path=path)
            self._col = self._client.get_or_create_collection(
                name=collection,
                metadata={"hnsw:space": "cosine", "embedding_model": embedding_model},
            )
        except Exception as exc:
            raise VectorStoreUnavailable(f"Cannot open vector store ({type(exc).__name__})") from exc
        pinned = (self._col.metadata or {}).get("embedding_model")
        if pinned and pinned != embedding_model:
            raise EmbeddingConfigMismatch(
                f"Vector index was built with embedding model '{pinned}' but '{embedding_model}' is configured. "
                "Re-index all documents or restore the original EMBEDDING_MODEL."
            )

    def ping(self) -> None:
        try:
            self._client.heartbeat()
        except Exception as exc:
            raise VectorStoreUnavailable(f"Vector store heartbeat failed ({type(exc).__name__})") from exc

    def add_chunks(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        try:
            for start in range(0, len(chunks), 128):
                part = chunks[start : start + 128]
                self._col.upsert(  # upsert => retrying ingestion cannot duplicate chunks
                    ids=[c.chunk_id for c in part],
                    embeddings=embeddings[start : start + 128],
                    documents=[c.text for c in part],
                    metadatas=[
                        {
                            "document_id": c.document_id,
                            "index_version": c.index_version,
                            "parent_id": c.parent_id,
                            "ordinal": c.ordinal,
                            "locator": _flatten_locator(c.locator),
                        }
                        for c in part
                    ],
                )
        except Exception as exc:
            raise VectorStoreUnavailable(f"Vector write failed ({type(exc).__name__})") from exc

    def delete_document(self, document_id: str) -> None:
        try:
            self._col.delete(where={"document_id": document_id})
        except Exception as exc:
            raise VectorStoreUnavailable(f"Vector delete failed ({type(exc).__name__})") from exc

    def count_document_chunks(self, document_id: str) -> int:
        try:
            return len(self._col.get(where={"document_id": document_id}, include=[])["ids"])
        except Exception as exc:
            raise VectorStoreUnavailable(f"Vector read failed ({type(exc).__name__})") from exc

    def query(self, embedding: list[float], document_ids: list[str], top_k: int) -> list[Hit]:
        if not document_ids:
            return []  # fail closed: never search without an explicit, non-empty permitted set
        hits: list[Hit] = []
        try:
            ids = list(document_ids)
            for start in range(0, len(ids), self.MAX_IN_BATCH):
                batch = ids[start : start + self.MAX_IN_BATCH]
                where = {"document_id": {"$in": batch}} if len(batch) > 1 else {"document_id": batch[0]}
                res = self._col.query(
                    query_embeddings=[embedding], n_results=top_k, where=where,
                    include=["metadatas", "distances"],
                )
                for cid, meta, dist in zip(res["ids"][0], res["metadatas"][0], res["distances"][0]):
                    hits.append(
                        Hit(
                            chunk_id=cid,
                            document_id=meta["document_id"],
                            index_version=int(meta["index_version"]),
                            parent_id=meta["parent_id"],
                            score=1.0 - float(dist),
                            locator=json.loads(meta.get("locator") or "{}"),
                        )
                    )
        except Exception as exc:
            raise VectorStoreUnavailable(f"Vector search failed ({type(exc).__name__})") from exc
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


class PineconeVectorStore:
    """Optional managed adapter (Pinecone v7+ SDK, serverless index, cosine metric).

    NOTE: implemented against the documented API but NOT exercised by this repository's
    automated tests (no Pinecone credentials in CI). Treat as unverified until you run
    the integration checklist in docs/DEPLOYMENT.md.
    """

    def __init__(self, api_key: str, index_name: str, namespace: str, embedding_model: str) -> None:
        from pinecone import Pinecone

        self._ns = namespace
        self._embedding_model = embedding_model
        try:
            self._index = Pinecone(api_key=api_key).Index(index_name)
        except Exception as exc:
            raise VectorStoreUnavailable(f"Cannot open Pinecone index ({type(exc).__name__})") from exc

    def ping(self) -> None:
        try:
            self._index.describe_index_stats()
        except Exception as exc:
            raise VectorStoreUnavailable(f"Pinecone unavailable ({type(exc).__name__})") from exc

    def add_chunks(self, chunks: list[ChunkRecord], embeddings: list[list[float]]) -> None:
        try:
            for start in range(0, len(chunks), 100):
                part = chunks[start : start + 100]
                self._index.upsert(
                    namespace=self._ns,
                    vectors=[
                        {
                            "id": c.chunk_id,
                            "values": embeddings[start + i],
                            "metadata": {
                                "document_id": c.document_id,
                                "index_version": c.index_version,
                                "parent_id": c.parent_id,
                                "ordinal": c.ordinal,
                                "locator": _flatten_locator(c.locator),
                                "embedding_model": self._embedding_model,
                            },
                        }
                        for i, c in enumerate(part)
                    ],
                )
        except Exception as exc:
            raise VectorStoreUnavailable(f"Pinecone write failed ({type(exc).__name__})") from exc

    def delete_document(self, document_id: str) -> None:
        try:
            self._index.delete(namespace=self._ns, filter={"document_id": {"$eq": document_id}})
        except Exception as exc:
            raise VectorStoreUnavailable(f"Pinecone delete failed ({type(exc).__name__})") from exc

    def count_document_chunks(self, document_id: str) -> int:
        raise NotImplementedError("count_document_chunks is not supported by the Pinecone adapter")

    def query(self, embedding: list[float], document_ids: list[str], top_k: int) -> list[Hit]:
        if not document_ids:
            return []
        hits: list[Hit] = []
        try:
            for start in range(0, len(document_ids), 500):
                batch = document_ids[start : start + 500]
                res = self._index.query(
                    namespace=self._ns, vector=embedding, top_k=top_k, include_metadata=True,
                    filter={"document_id": {"$in": batch}, "embedding_model": {"$eq": self._embedding_model}},
                )
                for m in res["matches"]:
                    meta = m["metadata"]
                    hits.append(
                        Hit(m["id"], meta["document_id"], int(meta["index_version"]), meta["parent_id"],
                            float(m["score"]), json.loads(meta.get("locator") or "{}"))
                    )
        except Exception as exc:
            raise VectorStoreUnavailable(f"Pinecone search failed ({type(exc).__name__})") from exc
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


def build_vector_store(cfg: dict, embedding_model: str) -> VectorStore:
    if cfg["VECTOR_BACKEND"] == "pinecone":
        return PineconeVectorStore(cfg["PINECONE_API_KEY"], cfg["PINECONE_INDEX"], cfg["PINECONE_NAMESPACE"],
                                   embedding_model)
    return ChromaVectorStore(cfg["CHROMA_DIR"], cfg["CHROMA_COLLECTION"], embedding_model)
