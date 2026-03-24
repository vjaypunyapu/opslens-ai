"""
OpsLens AI — Structure-Aware Chunker
======================================
Replaces the naive sliding-window tokeniser with source-aware chunking that:
  1. Preserves natural document boundaries (GitHub PR sections, Jira fields,
     Slack threads, code blocks, tables).
  2. Attaches rich metadata to every chunk (keywords, summary, hypothetical
     questions) to improve retrieval recall.

Hypothetical questions use the HyDE-reverse technique: at index time we
generate a few short questions that the chunk would answer.  At query time
the user's question then matches question-to-question rather than
question-to-document, which dramatically improves recall for conversational
queries.

Cost note:  LLM enrichment (summaries + hypothetical questions) is opt-in.
Set CHUNK_ENRICH_LLM=true in Railway to enable; it costs ~$0.001 per chunk
with GPT-4o-mini and is skipped gracefully if the key is unavailable.
"""
from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from typing import Any

import tiktoken
from openai import AsyncOpenAI

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_ENC             = tiktoken.get_encoding("cl100k_base")
HARD_MAX_TOKENS  = 512    # never exceed this per chunk
OVERLAP_TOKENS   = 64     # token overlap between adjacent chunks
ENRICH_ENABLED   = os.environ.get("CHUNK_ENRICH_LLM", "false").lower() == "true"
ENRICH_MODEL     = "gpt-4o-mini"   # cheap enrichment model

_openai_key = (
    os.environ.get("OPENAI_TOKEN", "")
    or os.environ.get("OPENAI_API_KEY", "")
    or (settings.OPENAI_API_KEY or "")
).strip()
_openai = AsyncOpenAI(api_key=_openai_key) if _openai_key else None


# ── Data model ────────────────────────────────────────────────────────────────
@dataclass
class StructuredChunk:
    content:               str
    source_type:           str
    chunk_type:            str          # e.g. "body", "code", "table", "comment"
    keywords:              list[str]    = field(default_factory=list)
    summary:               str          = ""
    hypothetical_questions: list[str]   = field(default_factory=list)
    metadata:              dict[str, Any] = field(default_factory=dict)


# ── Keyword extraction (lightweight, no LLM) ─────────────────────────────────
_STOP_WORDS = {
    "the","a","an","is","it","in","on","of","to","and","or","for","with",
    "this","that","was","are","be","been","has","have","had","will","would",
    "at","by","from","as","but","not","if","we","they","he","she","you","i",
    "can","do","did","so","up","out","more","also","than","when","what",
    "how","which","who","its","all","no","get","just","about","into","there",
}

def _extract_keywords(text: str, top_n: int = 8) -> list[str]:
    """Simple TF-based keyword extraction — no external dependencies."""
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in _STOP_WORDS:
            freq[w] = freq.get(w, 0) + 1
    # Also pull capitalised tokens as likely proper nouns / identifiers
    caps = re.findall(r"\b[A-Z][A-Z0-9_-]{1,}\b", text)
    for c in caps:
        freq[c.lower()] = freq.get(c.lower(), 0) + 3   # boost
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])][:top_n]


# ── LLM enrichment (optional) ─────────────────────────────────────────────────
async def _enrich_chunk(chunk: StructuredChunk) -> None:
    """Add LLM-generated summary and hypothetical questions to a chunk."""
    if not ENRICH_ENABLED or _openai is None:
        return
    if len(chunk.content) < 80:   # too short to enrich
        return
    try:
        resp = await _openai.chat.completions.create(
            model=ENRICH_MODEL,
            temperature=0.0,
            messages=[{
                "role": "system",
                "content": (
                    "You are a document indexing assistant. "
                    "Given a text chunk, output JSON with two keys:\n"
                    '  "summary": one sentence (max 30 words) describing the chunk,\n'
                    '  "questions": list of 3 short questions this chunk answers.\n'
                    "Output raw JSON only, no markdown."
                ),
            }, {
                "role": "user",
                "content": chunk.content[:1000],
            }],
        )
        import json
        data = json.loads(resp.choices[0].message.content or "{}")
        chunk.summary = data.get("summary", "")
        chunk.hypothetical_questions = data.get("questions", [])
    except Exception as exc:
        logger.warning("chunk enrichment failed: %s", exc)


# ── Token-aware sliding window (fallback) ────────────────────────────────────
def _sliding_window(text: str, source_type: str, chunk_type: str = "body") -> list[StructuredChunk]:
    tokens = _ENC.encode(text)
    if not tokens:
        return []
    chunks, start = [], 0
    while start < len(tokens):
        end = min(start + HARD_MAX_TOKENS, len(tokens))
        content = _ENC.decode(tokens[start:end])
        chunks.append(StructuredChunk(
            content=content,
            source_type=source_type,
            chunk_type=chunk_type,
            keywords=_extract_keywords(content),
        ))
        if end == len(tokens):
            break
        start += HARD_MAX_TOKENS - OVERLAP_TOKENS
    return chunks


# ── Source-specific chunkers ──────────────────────────────────────────────────

def _split_code_blocks(text: str) -> list[tuple[str, str]]:
    """Split text into (content, type) pairs preserving code blocks."""
    parts: list[tuple[str, str]] = []
    pattern = re.compile(r"```[\s\S]*?```", re.MULTILINE)
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            prose = text[last:m.start()].strip()
            if prose:
                parts.append((prose, "body"))
        parts.append((m.group(), "code"))
        last = m.end()
    if last < len(text):
        tail = text[last:].strip()
        if tail:
            parts.append((tail, "body"))
    return parts or [(text, "body")]


def chunk_github(content: str, metadata: dict) -> list[StructuredChunk]:
    """
    GitHub repos / issues / PRs.
    Splits: title line | code blocks | body prose | comments.
    """
    chunks: list[StructuredChunk] = []

    # Separate title from body (first line vs rest)
    lines  = content.split("\n", 1)
    title  = lines[0].strip()
    body   = lines[1].strip() if len(lines) > 1 else ""

    if title:
        chunks.append(StructuredChunk(
            content=title,
            source_type="github",
            chunk_type="title",
            keywords=_extract_keywords(title),
            metadata=metadata,
        ))

    for part_text, part_type in _split_code_blocks(body):
        # For long prose sections use sliding window; keep code blocks intact
        token_count = len(_ENC.encode(part_text))
        if part_type == "code" or token_count <= HARD_MAX_TOKENS:
            chunks.append(StructuredChunk(
                content=part_text,
                source_type="github",
                chunk_type=part_type,
                keywords=_extract_keywords(part_text),
                metadata=metadata,
            ))
        else:
            chunks.extend(_sliding_window(part_text, "github", part_type))

    return chunks or _sliding_window(content, "github")


def chunk_jira(content: str, metadata: dict) -> list[StructuredChunk]:
    """
    Jira issues.
    Splits: key+summary | status fields | description | comments.
    """
    chunks: list[StructuredChunk] = []

    # First line is always "[KEY] Summary"
    lines = content.split("\n", 1)
    header = lines[0].strip()
    rest   = lines[1].strip() if len(lines) > 1 else ""

    if header:
        chunks.append(StructuredChunk(
            content=header,
            source_type="jira",
            chunk_type="title",
            keywords=_extract_keywords(header),
            metadata=metadata,
        ))

    # Second line often has Status / Type / Project / Assignee metadata
    rest_lines = rest.split("\n")
    meta_line  = rest_lines[0].strip() if rest_lines else ""
    body_text  = "\n".join(rest_lines[1:]).strip() if len(rest_lines) > 1 else rest

    if meta_line and ("Status:" in meta_line or "Type:" in meta_line or "Project:" in meta_line):
        chunks.append(StructuredChunk(
            content=f"{header}\n{meta_line}",
            source_type="jira",
            chunk_type="metadata",
            keywords=_extract_keywords(meta_line),
            metadata=metadata,
        ))
    else:
        body_text = rest  # no structured meta line, treat all as body

    # Description / body
    if body_text:
        for sc in _sliding_window(body_text, "jira", "body"):
            sc.metadata = metadata
            chunks.append(sc)

    return chunks or _sliding_window(content, "jira")


def chunk_slack(content: str, metadata: dict) -> list[StructuredChunk]:
    """
    Slack messages.
    Each message is typically short; keep intact but tag with channel metadata.
    """
    token_count = len(_ENC.encode(content))
    if token_count <= HARD_MAX_TOKENS:
        return [StructuredChunk(
            content=content,
            source_type="slack",
            chunk_type="message",
            keywords=_extract_keywords(content),
            metadata=metadata,
        )]
    return _sliding_window(content, "slack", "message")


def chunk_generic(content: str, source_type: str, metadata: dict) -> list[StructuredChunk]:
    chunks = _sliding_window(content, source_type)
    for c in chunks:
        c.metadata = metadata
    return chunks


# ── Public API ────────────────────────────────────────────────────────────────
CHUNKERS = {
    "github":  chunk_github,
    "jira":    chunk_jira,
    "slack":   chunk_slack,
}


async def chunk_document(
    content: str,
    source_type: str,
    metadata: dict | None = None,
) -> list[StructuredChunk]:
    """
    Main entry point.  Returns enriched StructuredChunk list for a document.
    Enrichment (summary + hypothetical questions) is done in-place if enabled.
    """
    md = metadata or {}
    chunker = CHUNKERS.get(source_type)
    if chunker:
        chunks = chunker(content, md)
    else:
        chunks = chunk_generic(content, source_type, md)

    # Async enrichment
    if ENRICH_ENABLED and _openai is not None:
        import asyncio
        await asyncio.gather(*[_enrich_chunk(c) for c in chunks], return_exceptions=True)

    logger.info(
        "chunker: source=%s chunks=%d enriched=%s",
        source_type, len(chunks), ENRICH_ENABLED,
    )
    return chunks
