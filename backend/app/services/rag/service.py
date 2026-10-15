"""Secure RAG pipeline.

Order of operations (the security-relevant ones are marked [SEC]):
  1. [SEC] Resolve the caller's authorised READY document IDs from the relational DB.
  2. Embed the question.
  3. [SEC] Vector search *filtered to those IDs inside the vector store*.
  4. Threshold + top-k.
  5. [SEC] Revalidate every hit against the DB (ACL + READY + index_version) -- stale or
     inconsistent hits are dropped.
  6. Load parent passages (only for revalidated hits), add exact spreadsheet computations.
  7. Build the LLM context ONLY from steps 5-6.
  8. Generate; [SEC] verify cited passage ids against the supplied set; drop invalid ones.
  9. If nothing valid is cited, return the neutral no-evidence message.

Nothing about excluded documents (existence, names, counts) is ever returned or logged
beyond aggregate counters.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from ...core.metrics import metrics
from ...models import Document, ParentPassage
from ..audit import service as audit
from ..container import Services
from ..ingestion.pipeline import upload_path
from ..permissions import service as perms
from ..retrieval.embeddings import EmbeddingUnavailable
from ..retrieval.vectorstore import EmbeddingConfigMismatch, VectorStoreUnavailable
from .llm import LLMUnavailable
from .prompts import INSUFFICIENT_MARKER, NO_EVIDENCE_MESSAGE, SYSTEM_PROMPT, build_user_prompt
from .structured import try_structured
from ...extensions import db

log = logging.getLogger("app.rag")
_CITE_RE = re.compile(r"\[(S\d+)\]")
TABULAR = {"csv", "xlsx", "xls"}


class RagUnavailable(RuntimeError):
    """Retrieval or generation backend is down; the request must fail closed (safe message)."""


@dataclass
class Citation:
    id: str
    document_id: str
    passage_id: str | None
    locator: dict
    filename: str

    def stored(self) -> dict:
        return {"id": self.id, "document_id": self.document_id, "passage_id": self.passage_id,
                "locator": self.locator}


@dataclass
class RagResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    no_answer: bool = False


def locator_label(locator: dict) -> str:
    parts = []
    if "page" in locator:
        parts.append(f"p. {locator['page']}")
    if "section" in locator:
        parts.append(f"section: {locator['section']}")
    if "sheet" in locator:
        parts.append(f"sheet: {locator['sheet']}")
    if "rows" in locator:
        parts.append(f"rows {locator['rows']}")
    if locator.get("computed"):
        parts.append("computed")
    return ", ".join(parts)


class RagService:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.cfg = services.cfg

    # ------------------------------------------------------------------ retrieval
    def retrieve(self, user, question: str) -> list[dict]:
        """Return authorised context items: [{doc, passage_id, text, locator, score}]."""
        t0 = time.perf_counter()
        authorized = perms.authorized_document_ids(user)  # [SEC] step 1
        if not authorized:
            return []
        try:
            embedding = self.s.embedder.embed_query(question)
            top_k = self.cfg["TOP_K"]
            hits = self.s.vector_store.query(embedding, authorized, top_k * self.cfg["CANDIDATE_MULTIPLIER"])
        except (EmbeddingUnavailable, VectorStoreUnavailable, EmbeddingConfigMismatch) as exc:
            metrics.inc("retrieval_errors_total")
            raise RagUnavailable(str(exc)) from exc
        metrics.observe("retrieval_seconds", time.perf_counter() - t0)

        candidates = [h for h in hits if h.score >= self.cfg["SIMILARITY_THRESHOLD"]][:top_k]
        # [SEC] step 5: revalidate against the authoritative DB at the moment of use
        readable = perms.readable_ids_among(user, list({h.document_id for h in candidates}))
        docs = {d.id: d for d in Document.query.filter(Document.id.in_(readable)).all()} if readable else {}
        accepted, rejected = [], 0
        for h in candidates:
            doc = docs.get(h.document_id)
            if doc is None or h.index_version != doc.index_version:
                rejected += 1
                continue
            accepted.append(h)
        metrics.inc("retrieval_candidates_authorized_total", len(accepted))
        metrics.inc("retrieval_candidates_rejected_total", rejected)

        parent_ids = list(dict.fromkeys(h.parent_id for h in accepted))
        parents = (
            {p.id: p for p in ParentPassage.query.filter(ParentPassage.id.in_(parent_ids)).all()} if parent_ids else {}
        )
        items, seen = [], set()
        for h in accepted:
            parent = parents.get(h.parent_id)
            if parent is None or parent.document_id != h.document_id or parent.index_version != h.index_version:
                continue  # inconsistent index -> fail closed for this hit
            if parent.id in seen:
                continue
            seen.add(parent.id)
            items.append({"doc": docs[h.document_id], "passage_id": parent.id, "text": parent.text,
                          "locator": parent.locator, "score": h.score})

        # Exact spreadsheet computations over documents the user is already authorised for.
        for doc_id in {i["doc"].id for i in items if i["doc"].file_type in TABULAR}:
            doc = docs[doc_id]
            try:
                for r in try_structured(question, upload_path(doc.storage_name), doc.file_type):
                    items.insert(0, {"doc": doc, "passage_id": None, "text": r.text, "locator": r.locator,
                                     "score": 1.0})
            except Exception:  # computation is best-effort; vector evidence still stands
                log.exception("Structured query failed for document %s", doc.id)
        return items

    # ------------------------------------------------------------------ answer
    def answer(self, user, question: str) -> RagResult:
        metrics.inc("rag_answers_total")
        items = self.retrieve(user, question)

        # Build numbered context within the size budget.
        passages, budget = [], self.cfg["MAX_CONTEXT_CHARS"]
        for item in items:
            text = item["text"][: max(0, budget)]
            if not text:
                break
            budget -= len(text)
            sid = f"S{len(passages) + 1}"
            label = f'{item["doc"].original_filename}'
            loc = locator_label(item["locator"])
            if loc:
                label += f" ({loc})"
            passages.append({"id": sid, "label": label, "text": text, "item": item})

        if not passages:
            return self._no_answer()

        prompt = build_user_prompt(question, [{k: p[k] for k in ("id", "label", "text")} for p in passages])
        try:
            result = self.s.llm.generate(SYSTEM_PROMPT, prompt)
        except LLMUnavailable as exc:
            metrics.inc("model_errors_total")
            raise RagUnavailable(str(exc)) from exc
        if result.input_tokens:
            metrics.inc("llm_input_tokens_total", result.input_tokens)
        if result.output_tokens:
            metrics.inc("llm_output_tokens_total", result.output_tokens)

        text = (result.text or "").strip()
        if not text or INSUFFICIENT_MARKER in text:
            return self._no_answer()

        by_id = {p["id"]: p for p in passages}
        cited = [c for c in dict.fromkeys(_CITE_RE.findall(text)) if c in by_id]  # [SEC] step 8
        # strip markers that point to ids we never supplied (fabricated citations)
        text = _CITE_RE.sub(lambda m: m.group(0) if m.group(1) in by_id else "", text).strip()
        if not cited:
            return self._no_answer()  # ungrounded answer: refuse rather than present unsupported claims

        citations = [
            Citation(
                id=sid,
                document_id=by_id[sid]["item"]["doc"].id,
                passage_id=by_id[sid]["item"]["passage_id"],
                locator=by_id[sid]["item"]["locator"],
                filename=by_id[sid]["item"]["doc"].original_filename,
            )
            for sid in cited
        ]
        metrics.inc("rag_answers_with_valid_citation_total")
        return RagResult(answer=text, citations=citations)

    @staticmethod
    def _no_answer() -> RagResult:
        metrics.inc("rag_no_answer_total")
        return RagResult(answer=NO_EVIDENCE_MESSAGE, no_answer=True)
