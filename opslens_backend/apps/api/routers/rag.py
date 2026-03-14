"""
OpsLens AI — RAG Chat Router
==============================
Handles chat session management and streaming question-answering.

Endpoints:
    POST   /api/v1/chat/sessions            — Create session
    GET    /api/v1/chat/sessions            — List sessions
    GET    /api/v1/chat/sessions/{id}       — Get session + messages
    POST   /api/v1/chat/sessions/{id}/query — Submit query (SSE stream)
    DELETE /api/v1/chat/sessions/{id}       — Delete session
    POST   /api/v1/chat/feedback            — Submit message feedback
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_member, require_viewer
from ..db.session import get_db
from ..models.chat import ChatMessage, ChatSession
from ..services.rag_service import RagService, get_rag_service
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Request / Response schemas ────────────────────────────────────────────────
class QueryFilters(BaseModel):
    source_types: list[str] | None = Field(
        default=None,
        description="Limit search to specific source types (slack, jira, gdrive, etc.)",
        examples=[["slack", "jira"]],
    )
    date_from: str | None = Field(default=None, description="ISO date string YYYY-MM-DD")
    date_to:   str | None = Field(default=None, description="ISO date string YYYY-MM-DD")


class QueryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="User question")
    filters: QueryFilters | None = None


class FeedbackRequest(BaseModel):
    message_id: str
    score: int = Field(..., ge=-1, le=1, description="-1 = negative, 1 = positive")


class SessionOut(BaseModel):
    id: str
    title: str | None
    message_count: int
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    source_doc_ids: list[str]
    feedback: int | None
    created_at: str


# ── Create session ─────────────────────────────────────────────────────────────
@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """Create a new chat session for the authenticated user."""
    session = ChatSession(
        id=str(uuid.uuid4()),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("Created chat session %s for user %s", session.id, ctx.user_id)
    return _session_to_out(session, 0)


# ── List sessions ──────────────────────────────────────────────────────────────
@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
    limit: int = 20,
    offset: int = 0,
):
    """List the current user's chat sessions, newest first."""
    result = await db.execute(
        sa.select(ChatSession)
        .where(
            ChatSession.tenant_id == ctx.tenant_id,
            ChatSession.user_id == ctx.user_id,
        )
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    sessions = result.scalars().all()
    # Count messages per session efficiently
    counts_result = await db.execute(
        sa.select(
            ChatMessage.session_id,
            sa.func.count(ChatMessage.id).label("n"),
        )
        .where(ChatMessage.session_id.in_([s.id for s in sessions]))
        .group_by(ChatMessage.session_id)
    )
    counts = {row.session_id: row.n for row in counts_result}
    return [_session_to_out(s, counts.get(str(s.id), 0)) for s in sessions]


# ── Get session with messages ──────────────────────────────────────────────────
@router.get("/sessions/{session_id}", response_model=dict)
async def get_session(
    session_id: str,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """Get a session and its full message history."""
    session = await _get_session_or_404(db, session_id, ctx.tenant_id)
    msgs_result = await db.execute(
        sa.select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at)
    )
    messages = [_message_to_out(m) for m in msgs_result.scalars().all()]
    return {**_session_to_out(session, len(messages)).__dict__, "messages": messages}


# ── Query (streaming SSE) ──────────────────────────────────────────────────────
@router.post("/sessions/{session_id}/query")
async def query_session(
    session_id: str,
    body: QueryRequest,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
    rag: RagService = Depends(get_rag_service),
):
    """
    Submit a question. Returns a **Server-Sent Events** stream.

    Event types emitted (newline-delimited JSON):
    - `{"type": "token",   "data": "<text fragment>"}`
    - `{"type": "sources", "data": [{"title", "url", "source_type", "snippet"}]}`
    - `{"type": "done",    "latency_ms": <int>}`
    - `{"type": "error",   "message": "<description>"}`
    """
    import time
    session = await _get_session_or_404(db, session_id, ctx.tenant_id)

    # Load recent history (last 5 turns = 10 messages)
    hist_result = await db.execute(
        sa.select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(10)
    )
    history = [
        {"role": m.role, "content": m.content}
        for m in reversed(hist_result.scalars().all())
    ]

    # Persist user message
    user_msg = ChatMessage(
        id=str(uuid.uuid4()),
        session_id=str(session.id),
        role="user",
        content=body.content,
    )
    db.add(user_msg)

    if not session.title:
        session.title = body.content[:60] + ("…" if len(body.content) > 60 else "")

    await db.commit()
    logger.info("Query submitted to session %s | tenant=%s", session_id, ctx.tenant_id)

    source_types = body.filters.source_types if body.filters else None
    start = time.monotonic()

    async def event_stream():
        tokens: list[str] = []
        try:
            async for event in rag.stream(
                tenant_id=ctx.tenant_id,
                company_name=ctx.company_name,
                question=body.content,
                history=history,
                source_types=source_types,
            ):
                if event["type"] == "token":
                    tokens.append(event["data"])
                yield f"data: {json.dumps(event)}\n\n"

            # Persist assistant message
            assistant_content = "".join(tokens)
            if assistant_content:
                async with db.begin_nested():
                    db.add(ChatMessage(
                        id=str(uuid.uuid4()),
                        session_id=str(session.id),
                        role="assistant",
                        content=assistant_content,
                        latency_ms=int((time.monotonic() - start) * 1000),
                    ))
                    await db.flush()
                await db.commit()

        except Exception as exc:
            logger.exception("Streaming error in session %s", session_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
        },
    )


# ── Delete session ─────────────────────────────────────────────────────────────
@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    session = await _get_session_or_404(db, session_id, ctx.tenant_id)
    await db.delete(session)
    await db.commit()


# ── Feedback ───────────────────────────────────────────────────────────────────
@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def submit_feedback(
    body: FeedbackRequest,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """Record thumbs-up/down on a specific assistant message."""
    result = await db.execute(
        sa.select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatMessage.id == body.message_id,
            ChatSession.tenant_id == ctx.tenant_id,
            ChatMessage.role == "assistant",
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.feedback = body.score
    await db.commit()
    logger.info("Feedback %d on message %s", body.score, body.message_id)


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _get_session_or_404(db, session_id: str, tenant_id: str) -> ChatSession:
    result = await db.execute(
        sa.select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tenant_id == tenant_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


def _session_to_out(s: ChatSession, message_count: int) -> SessionOut:
    return SessionOut(
        id=str(s.id),
        title=s.title,
        message_count=message_count,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
    )


def _message_to_out(m: ChatMessage) -> MessageOut:
    return MessageOut(
        id=str(m.id),
        role=m.role,
        content=m.content,
        source_doc_ids=[str(d) for d in (m.source_doc_ids or [])],
        feedback=m.feedback,
        created_at=m.created_at.isoformat(),
    )
