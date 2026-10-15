"""Holds the heavyweight service singletons (embedder, vector store, chat model).

The vector store is opened lazily so the app can boot (and report `not ready`) even if the
vector database is down. Tests replace attributes on the container to inject fakes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from flask import current_app

from .rag.llm import ChatModel, build_chat_model
from .retrieval.embeddings import Embedder, build_embedder
from .retrieval.vectorstore import VectorStore, build_vector_store


@dataclass
class Services:
    cfg: dict
    embedder: Embedder
    llm: ChatModel
    _vector_store: VectorStore | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            with self._lock:
                if self._vector_store is None:
                    self._vector_store = build_vector_store(self.cfg, self.embedder.model_name)
        return self._vector_store

    @vector_store.setter
    def vector_store(self, value: VectorStore) -> None:
        self._vector_store = value


def build_services(cfg: dict) -> Services:
    return Services(cfg=cfg, embedder=build_embedder(cfg), llm=build_chat_model(cfg))


def get_services() -> Services:
    return current_app.extensions["services"]
