"""Embedding providers. One interface, OpenAI for real use, deterministic fake for tests."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class EmbeddingUnavailable(RuntimeError):
    """Embedding backend is misconfigured or unreachable (message is safe to show)."""


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by do does for from has have how i in is it its of on or that the this to was what when "
    "where which who will with you your me my we our us can about tell show give please all any".split()
)


class FakeEmbeddings:
    """Deterministic hashed bag-of-words embeddings. For tests/dev only -- no network."""

    DIM = 384

    def __init__(self, model_name: str = "fake-hash-384") -> None:
        self.model_name = model_name

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for tok in _TOKEN_RE.findall(text.lower()):
            if tok in _STOPWORDS:
                continue
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
            vec[h % self.DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class OpenAIEmbedder:
    def __init__(self, model: str, api_key: str | None, base_url: str | None, timeout: int) -> None:
        self.model_name = model
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._client = None

    def _get(self):
        if not self._api_key:
            raise EmbeddingUnavailable("Embedding model is not configured: OPENAI_API_KEY is missing")
        if self._client is None:
            from langchain_openai import OpenAIEmbeddings

            kwargs = {"model": self.model_name, "api_key": self._api_key, "timeout": self._timeout, "max_retries": 2}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAIEmbeddings(**kwargs)
        return self._client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._get().embed_documents(texts)
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # network, auth, rate limit ...
            raise EmbeddingUnavailable(f"Embedding service error ({type(exc).__name__})") from exc

    def embed_query(self, text: str) -> list[float]:
        try:
            return self._get().embed_query(text)
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(f"Embedding service error ({type(exc).__name__})") from exc


def build_embedder(cfg: dict) -> Embedder:
    if cfg["EMBEDDINGS_PROVIDER"] == "fake":
        return FakeEmbeddings()
    return OpenAIEmbedder(cfg["EMBEDDING_MODEL"], cfg.get("OPENAI_API_KEY"), cfg.get("OPENAI_BASE_URL"),
                          cfg["MODEL_TIMEOUT_SECONDS"])
