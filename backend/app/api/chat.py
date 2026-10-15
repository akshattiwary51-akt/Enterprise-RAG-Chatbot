from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from ..auth.decorators import login_required
from ..core.errors import ApiError, not_found
from ..extensions import db, limiter
from ..models import Conversation, Document, Message
from ..schemas import ChatSchema, PageSchema, load_json
from ..services.audit import service as audit
from ..services.container import get_services
from ..services.permissions import service as perms
from ..services.rag.prompts import REDACTED_MESSAGE
from ..services.rag.service import RagService, RagUnavailable, locator_label
from .serializers import paginate

bp = Blueprint("chat", __name__, url_prefix="/api")


def _owned_conversation(conversation_id: str) -> Conversation:
    conv = Conversation.query.filter_by(id=conversation_id, user_id=g.current_user.id).first()
    if conv is None:  # same answer for "doesn't exist" and "someone else's"
        raise not_found("Conversation not found")
    return conv


def render_message(msg: Message, readable_docs: dict[str, Document]) -> dict:
    """Serialise a message, re-checking *current* access for every cited document.

    If any cited document is no longer readable (ACL revoked, role removed, deleted, not READY)
    the assistant answer is withheld entirely -- the text may paraphrase protected content --
    and replaced with a neutral notice.
    """
    base = {"id": msg.id, "role": msg.role, "created_at": msg.created_at.isoformat(), "no_answer": msg.no_answer}
    if msg.role != "assistant":
        return {**base, "content": msg.content, "citations": [], "redacted": False}
    cites = msg.citations or []
    if any(c["document_id"] not in readable_docs for c in cites):
        return {**base, "content": REDACTED_MESSAGE, "citations": [], "redacted": True}
    out = []
    for c in cites:
        doc = readable_docs[c["document_id"]]
        out.append({"id": c["id"], "document_id": doc.id, "filename": doc.original_filename,
                    "passage_id": c.get("passage_id"), "location": locator_label(c.get("locator", {}))})
    return {**base, "content": msg.content, "citations": out, "redacted": False}


def _readable_docs_for(messages: list[Message]) -> dict[str, Document]:
    ids = list({c["document_id"] for m in messages if m.role == "assistant" for c in (m.citations or [])})
    allowed = perms.readable_ids_among(g.current_user, ids)
    return {d.id: d for d in Document.query.filter(Document.id.in_(allowed)).all()} if allowed else {}


@bp.post("/chat")
@login_required
@limiter.limit(lambda: current_app.config["CHAT_RATE_LIMIT"], key_func=lambda: g.current_user.id)
def chat():
    """Ask a question. Identity and permissions come from the token only.
    ---
    tags: [Chat]
    responses: {200: {description: Answer with verified citations}, 404: {description: Unknown conversation}, 503: {description: AI backend unavailable}}
    """
    user = g.current_user
    data = load_json(ChatSchema(), request.get_json(silent=True))
    question = data["message"].strip()
    if len(question) > current_app.config["MAX_QUESTION_CHARS"]:
        raise ApiError(400, "validation_error", "Message is too long")

    conv = _owned_conversation(data["conversation_id"]) if data.get("conversation_id") else None

    try:
        result = RagService(get_services()).answer(user, question)
    except RagUnavailable as exc:
        audit.record("chat.error", result="failure", actor=user, details={"reason": str(exc)[:150]})
        # RagUnavailable messages are written to be safe to show (e.g. "Chat model is not configured:
        # OPENAI_API_KEY is missing"); they never contain document text or credentials.
        raise ApiError(503, "ai_unavailable", f"The assistant is temporarily unavailable ({exc}). "
                       "No document content was exposed; please retry shortly.")

    if conv is None:
        conv = Conversation(user_id=user.id, title=question[:60] + ("…" if len(question) > 60 else ""))
        db.session.add(conv)
        db.session.flush()
    n = len(conv.messages)
    db.session.add(Message(conversation_id=conv.id, seq=n, role="user", content=question))
    assistant = Message(conversation_id=conv.id, seq=n + 1, role="assistant", content=result.answer,
                        citations=[c.stored() for c in result.citations], no_answer=result.no_answer)
    db.session.add(assistant)
    conv.updated_at = assistant.created_at or conv.updated_at
    db.session.commit()

    docs = _readable_docs_for([assistant])
    audit.record("chat.query", actor=user, target_type="conversation", target_id=conv.id,
                 details={"citations": len(result.citations), "no_answer": result.no_answer})
    return jsonify({"conversation_id": conv.id, "message": render_message(assistant, docs)})


@bp.get("/conversations")
@login_required
def list_conversations():
    """List the caller's own conversations.
    ---
    tags: [Chat]
    responses: {200: {description: Paginated conversations}}
    """
    args = PageSchema().load(request.args)
    q = Conversation.query.filter_by(user_id=g.current_user.id).order_by(Conversation.updated_at.desc())
    return jsonify(paginate(q, args["page"], args["per_page"],
                            lambda c: {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}))


@bp.get("/conversations/<conversation_id>")
@login_required
def get_conversation(conversation_id: str):
    """Fetch one of the caller's conversations; answers citing now-inaccessible documents are withheld.
    ---
    tags: [Chat]
    responses: {200: {description: Conversation}, 404: {description: Not found}}
    """
    conv = _owned_conversation(conversation_id)
    docs = _readable_docs_for(conv.messages)
    return jsonify({"id": conv.id, "title": conv.title,
                    "messages": [render_message(m, docs) for m in conv.messages]})


@bp.delete("/conversations/<conversation_id>")
@login_required
def delete_conversation(conversation_id: str):
    """Delete one of the caller's conversations.
    ---
    tags: [Chat]
    responses: {200: {description: Deleted}, 404: {description: Not found}}
    """
    conv = _owned_conversation(conversation_id)
    db.session.delete(conv)
    db.session.commit()
    return jsonify({"status": "deleted", "id": conversation_id})
