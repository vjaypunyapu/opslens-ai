"""
OpsLens AI — Structure-Aware Chunker v2
=========================================
Replaces the 512-token sliding window with a two-stage pipeline:

  Stage 1 — Parse
    The raw document text is parsed into a structural tree of Sections,
    each containing typed Blocks (paragraph, code, table, list, metadata).
    Headings anchor their content; tables and code blocks are atomic units.

  Stage 2 — Chunk
    Each Block is bounded to HARD_MAX_TOKENS.  The key rules are:
      • Tables   — never split; if > HARD_MAX_TOKENS, kept whole with a warning
      • Code     — never split; language tag preserved
      • Headings — carried into the first chunk of their section AND stored in
                   chunk metadata so every chunk knows its parent heading
      • Prose    — split at paragraph boundaries (double newline) first;
                   only falls back to token-level splits if a single paragraph
                   still exceeds HARD_MAX_TOKENS (rare)
      • Overlap  — implemented by re-attaching the parent heading + last
                   paragraph of the previous chunk rather than raw token overlap,
                   so every chunk is semantically self-contained

  Stage 3 — Enrich (always-on)
    All chunks for a document are enriched in a SINGLE batched GPT-4o-mini
    call that generates 3 hypothetical questions per chunk.  Questions are
    phrased as what a user would type in the chat UI ("what is the status of
    AUTH-123?"), enabling question-to-question matching at retrieval time
    (HyDE-reverse).  This costs ~$0.002 per typical document and is not
    optional — it is what makes the hybrid retriever precise.

Source-specific parsers:
  GitHub  — Markdown-aware: H1/H2/H3 headings, fenced code, GFM tables,
             ordered/unordered lists.  Title and body kept together.
  Jira    — Field-line parser: key+summary as title, structured metadata
             line (Status/Type/Assignee), description paragraphs.
  Slack   — Thread-aware: messages kept intact; long threads split at
             message boundaries, not mid-message.
  Generic — Falls back to the Markdown parser; works well for any prose.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import tiktoken

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_ENC             = tiktoken.get_encoding("cl100k_base")
HARD_MAX_TOKENS  = 512    # hard ceiling per chunk (embedding context window safe)
SOFT_MAX_TOKENS  = 400    # preferred max — leave headroom for heading prefix
OVERLAP_TOKENS   = 64     # token overlap when prose forces a mid-paragraph split
HYDE_MODEL       = "gpt-4o-mini"
HYDE_QUESTIONS   = 3      # questions per chunk

_openai_key = (
    os.environ.get("OPENAI_TOKEN", "")
    or os.environ.get("OPENAI_API_KEY", "")
    or (settings.OPENAI_API_KEY or "")
).strip()


# ── Data models ───────────────────────────────────────────────────────────────
@dataclass
class Block:
    """Smallest structural unit within a section."""
    type:     str   # "paragraph" | "code" | "table" | "list" | "metadata" | "heading"
    content:  str
    language: str = ""   # populated for code blocks


@dataclass
class Section:
    """A heading and all the blocks that belong under it."""
    heading:  str         # "" if document has no headings
    level:    int         # 1, 2, 3 … (0 = no heading)
    blocks:   list[Block] = field(default_factory=list)


@dataclass
class StructuredChunk:
    """Enriched chunk ready for embedding and upsert."""
    content:               str
    source_type:           str
    chunk_type:            str
    keywords:              list[str]      = field(default_factory=list)
    summary:               str            = ""
    hypothetical_questions: list[str]    = field(default_factory=list)
    metadata:              dict[str, Any] = field(default_factory=dict)


# ── Keyword extraction (no LLM, no deps) ─────────────────────────────────────
_STOP = {
    "the","a","an","is","it","in","on","of","to","and","or","for","with",
    "this","that","was","are","be","been","has","have","had","will","would",
    "at","by","from","as","but","not","if","we","they","he","she","you","i",
    "can","do","did","so","up","out","more","also","than","when","what",
    "how","which","who","its","all","no","get","just","about","into","there",
}

def _keywords(text: str, top_n: int = 8) -> list[str]:
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in _STOP:
            freq[w] = freq.get(w, 0) + 1
    # Boost identifiers / proper nouns (all-caps or CamelCase)
    for tok in re.findall(r"\b[A-Z][A-Z0-9_-]{1,}\b|\b[A-Z][a-z]+[A-Z]\w*\b", text):
        freq[tok.lower()] = freq.get(tok.lower(), 0) + 3
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])][:top_n]


def _token_len(text: str) -> int:
    return len(_ENC.encode(text))


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — PARSE
# ═══════════════════════════════════════════════════════════════════════════════

# ── Markdown / generic parser ─────────────────────────────────────────────────
_FENCE_RE    = re.compile(r"^```(\w*)\n([\s\S]*?)```\s*$", re.MULTILINE)
_TABLE_ROW   = re.compile(r"^\|.+\|$")
_HEADING_RE  = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_ITEM   = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s+")


def _parse_markdown(text: str) -> list[Section]:
    """
    Parse a Markdown document into a list of Sections.
    Each heading starts a new Section; blocks without a heading go into
    a synthetic Section(heading="", level=0).
    """
    # Replace fenced code blocks with placeholders so we don't confuse the
    # line scanner, then restore them per-block.
    code_blocks: list[tuple[str, str]] = []
    def _stash_code(m: re.Match) -> str:
        lang = m.group(1)
        body = m.group(2)
        idx  = len(code_blocks)
        code_blocks.append((lang, body.rstrip()))
        return f"\x00CODE{idx}\x00"
    cleaned = _FENCE_RE.sub(_stash_code, text)

    sections: list[Section]   = [Section(heading="", level=0)]
    current_table: list[str]  = []
    current_list:  list[str]  = []
    current_para:  list[str]  = []

    def _flush_para():
        if current_para:
            joined = "\n".join(current_para).strip()
            if joined:
                sections[-1].blocks.append(Block("paragraph", joined))
            current_para.clear()

    def _flush_list():
        if current_list:
            joined = "\n".join(current_list).strip()
            if joined:
                sections[-1].blocks.append(Block("list", joined))
            current_list.clear()

    def _flush_table():
        if current_table:
            joined = "\n".join(current_table).strip()
            if joined:
                sections[-1].blocks.append(Block("table", joined))
            current_table.clear()

    for raw_line in cleaned.split("\n"):
        line = raw_line.rstrip()

        # ── Inline code block placeholder
        if "\x00CODE" in line:
            _flush_para()
            _flush_list()
            _flush_table()
            idx = int(re.search(r"\x00CODE(\d+)\x00", line).group(1))
            lang, body = code_blocks[idx]
            sections[-1].blocks.append(Block("code", body, language=lang))
            continue

        # ── Heading
        m = _HEADING_RE.match(line)
        if m:
            _flush_para()
            _flush_list()
            _flush_table()
            level   = len(m.group(1))
            heading = m.group(2).strip()
            sections.append(Section(heading=heading, level=level))
            continue

        # ── Table row
        if _TABLE_ROW.match(line):
            _flush_para()
            _flush_list()
            current_table.append(line)
            continue
        elif current_table:
            _flush_table()

        # ── List item
        if _LIST_ITEM.match(line):
            _flush_para()
            current_list.append(line)
            continue
        elif current_list and line.startswith("  "):  # continuation indent
            current_list.append(line)
            continue
        elif current_list:
            _flush_list()

        # ── Blank line: paragraph boundary
        if line.strip() == "":
            _flush_para()
            continue

        current_para.append(line)

    _flush_para()
    _flush_list()
    _flush_table()

    # Drop empty leading section
    return [s for s in sections if s.blocks or s.heading]


# ── Jira parser ───────────────────────────────────────────────────────────────
def _parse_jira(content: str) -> list[Section]:
    """
    Jira content format (from direct_sync_service):
        [KEY] Summary
        Status: X  Type: Y  Project: Z  Assignee: A

        <description paragraphs...>
    """
    lines      = content.split("\n")
    title_line = lines[0].strip() if lines else ""
    rest       = "\n".join(lines[1:]).strip()

    sections: list[Section] = []

    # Title block
    if title_line:
        sections.append(Section(
            heading=title_line, level=1,
            blocks=[Block("heading", title_line)],
        ))

    # Metadata line (Status/Type/...)
    meta_section = Section(heading="Metadata", level=2)
    body_lines   = rest.split("\n")
    body_start   = 0
    if body_lines and re.search(r"Status:|Type:|Project:|Assignee:", body_lines[0]):
        meta_section.blocks.append(Block("metadata", f"{title_line}\n{body_lines[0].strip()}"))
        body_start = 1

    if meta_section.blocks:
        sections.append(meta_section)

    # Description body — delegate to markdown parser for sub-structure
    body_text = "\n".join(body_lines[body_start:]).strip()
    if body_text:
        desc_sections = _parse_markdown(body_text)
        # Re-level so description sections sit below the title
        for s in desc_sections:
            s.level = max(s.level, 2)
            sections.append(s)

    return sections or [Section(heading="", level=0, blocks=[Block("paragraph", content)])]


# ── Slack parser ──────────────────────────────────────────────────────────────
def _parse_slack(content: str) -> list[Section]:
    """
    Slack messages are typically short and already atomic.
    No structural parsing needed; return a single section.
    """
    return [Section(
        heading="",
        level=0,
        blocks=[Block("message", content)],
    )]


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — CHUNK
# ═══════════════════════════════════════════════════════════════════════════════

def _token_split_prose(text: str, soft_max: int, overlap: int) -> list[str]:
    """
    Split prose text at paragraph boundaries first; only if a single paragraph
    still exceeds soft_max does it fall back to token-level splitting.
    Returns a list of text chunks.
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []

    result: list[str]  = []
    current_paras: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _token_len(para)

        # Single paragraph already too big — must split at token level
        if para_tokens > soft_max:
            if current_paras:
                result.append("\n\n".join(current_paras))
                current_paras = []
                current_tokens = 0
            tokens = _ENC.encode(para)
            start  = 0
            while start < len(tokens):
                end     = min(start + soft_max, len(tokens))
                result.append(_ENC.decode(tokens[start:end]))
                if end == len(tokens):
                    break
                start  += soft_max - overlap
            continue

        # Adding this paragraph would exceed the limit — flush first
        if current_tokens + para_tokens > soft_max and current_paras:
            result.append("\n\n".join(current_paras))
            # Overlap: carry the last paragraph of the previous chunk forward
            overlap_para   = current_paras[-1]
            current_paras  = [overlap_para, para]
            current_tokens = _token_len(overlap_para) + para_tokens
        else:
            current_paras.append(para)
            current_tokens += para_tokens

    if current_paras:
        result.append("\n\n".join(current_paras))

    return result


def _section_prefix(section: Section) -> str:
    """Return the heading prefix to prepend to chunks (for context anchoring)."""
    if not section.heading:
        return ""
    prefix = "#" * max(section.level, 1) + " " + section.heading
    return prefix + "\n\n"


def _sections_to_chunks(
    sections: list[Section],
    source_type: str,
    base_metadata: dict,
) -> list[StructuredChunk]:
    chunks: list[StructuredChunk] = []

    for section in sections:
        prefix     = _section_prefix(section)
        prefix_tok = _token_len(prefix)
        soft_max   = SOFT_MAX_TOKENS - prefix_tok

        for block in section.blocks:

            # ── Heading blocks (the heading itself as a standalone anchor chunk)
            if block.type == "heading":
                # Already represented as the section prefix; skip as a separate chunk
                # unless it's the only block (standalone heading = title chunk)
                if len(section.blocks) == 1:
                    chunks.append(StructuredChunk(
                        content=block.content,
                        source_type=source_type,
                        chunk_type="title",
                        keywords=_keywords(block.content),
                        metadata={**base_metadata, "section_heading": section.heading},
                    ))
                continue

            # ── Code blocks — always atomic
            if block.type == "code":
                lang_tag  = f"```{block.language}\n" if block.language else "```\n"
                full_code = lang_tag + block.content + "\n```"
                content   = prefix + full_code if prefix else full_code
                chunks.append(StructuredChunk(
                    content=content,
                    source_type=source_type,
                    chunk_type="code",
                    keywords=_keywords(block.content),
                    metadata={**base_metadata, "section_heading": section.heading,
                               "language": block.language},
                ))
                continue

            # ── Tables — always atomic (warn if huge)
            if block.type == "table":
                content = prefix + block.content if prefix else block.content
                if _token_len(content) > HARD_MAX_TOKENS:
                    logger.warning(
                        "chunker: table exceeds HARD_MAX (%d tokens); kept whole",
                        _token_len(content),
                    )
                chunks.append(StructuredChunk(
                    content=content,
                    source_type=source_type,
                    chunk_type="table",
                    keywords=_keywords(block.content),
                    metadata={**base_metadata, "section_heading": section.heading},
                ))
                continue

            # ── Metadata blocks — always atomic (short by nature)
            if block.type == "metadata":
                chunks.append(StructuredChunk(
                    content=block.content,
                    source_type=source_type,
                    chunk_type="metadata",
                    keywords=_keywords(block.content),
                    metadata={**base_metadata, "section_heading": section.heading},
                ))
                continue

            # ── Slack messages — atomic
            if block.type == "message":
                if _token_len(block.content) <= HARD_MAX_TOKENS:
                    chunks.append(StructuredChunk(
                        content=block.content,
                        source_type=source_type,
                        chunk_type="message",
                        keywords=_keywords(block.content),
                        metadata=base_metadata,
                    ))
                else:
                    # Long thread: split at message boundaries (\n---\n) or tokens
                    for part in _token_split_prose(block.content, SOFT_MAX_TOKENS, OVERLAP_TOKENS):
                        chunks.append(StructuredChunk(
                            content=part,
                            source_type=source_type,
                            chunk_type="message",
                            keywords=_keywords(part),
                            metadata=base_metadata,
                        ))
                continue

            # ── Lists — keep together if small; split otherwise
            if block.type == "list":
                content = prefix + block.content if prefix else block.content
                if _token_len(content) <= HARD_MAX_TOKENS:
                    chunks.append(StructuredChunk(
                        content=content,
                        source_type=source_type,
                        chunk_type="list",
                        keywords=_keywords(block.content),
                        metadata={**base_metadata, "section_heading": section.heading},
                    ))
                else:
                    items = [line for line in block.content.split("\n") if line.strip()]
                    current_items: list[str] = []
                    current_tok = prefix_tok
                    for item in items:
                        item_tok = _token_len(item)
                        if current_tok + item_tok > SOFT_MAX_TOKENS and current_items:
                            text_out = prefix + "\n".join(current_items)
                            chunks.append(StructuredChunk(
                                content=text_out,
                                source_type=source_type,
                                chunk_type="list",
                                keywords=_keywords(text_out),
                                metadata={**base_metadata, "section_heading": section.heading},
                            ))
                            current_items = [item]
                            current_tok   = prefix_tok + item_tok
                        else:
                            current_items.append(item)
                            current_tok += item_tok
                    if current_items:
                        text_out = prefix + "\n".join(current_items)
                        chunks.append(StructuredChunk(
                            content=text_out,
                            source_type=source_type,
                            chunk_type="list",
                            keywords=_keywords(text_out),
                            metadata={**base_metadata, "section_heading": section.heading},
                        ))
                continue

            # ── Paragraphs — split at natural boundaries first
            text_parts = _token_split_prose(block.content, soft_max, OVERLAP_TOKENS)
            for part in text_parts:
                content = prefix + part if prefix else part
                chunks.append(StructuredChunk(
                    content=content,
                    source_type=source_type,
                    chunk_type="body",
                    keywords=_keywords(part),
                    metadata={**base_metadata, "section_heading": section.heading},
                ))

    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — ENRICH  (always-on HyDE question generation)
# ═══════════════════════════════════════════════════════════════════════════════

_HYDE_SYSTEM = """\
You are a search-index assistant.  For each numbered chunk below, generate
exactly {n} short, natural questions that a user would type into a search box
to find that chunk.  Questions should be specific, not generic.  Examples:
  "what is the status of AUTH-123?"
  "how does the payment webhook retry logic work?"
  "who is assigned to the login bug in sprint 14?"

Output JSON only — a list of objects with key "q" (list of strings), one
per chunk, in the same order:
  [{{"q": ["q1","q2","q3"]}}, {{"q": ["q1","q2","q3"]}}, ...]
No markdown, no explanation.""".replace("{n}", str(HYDE_QUESTIONS))


async def _enrich_batch(chunks: list[StructuredChunk]) -> None:
    """
    Enrich all chunks for a single document in ONE API call.
    Chunks that are too short (< 60 chars) are skipped; they receive empty
    hypothetical_questions and the caller keeps them as-is.
    """
    if not _openai_key:
        return

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=_openai_key)

    # Only enrich chunks with enough substance
    to_enrich = [c for c in chunks if len(c.content) >= 60]
    if not to_enrich:
        return

    # Build the numbered chunk list for the prompt
    numbered = "\n\n".join(
        f"[{i+1}] {c.content[:600]}"
        for i, c in enumerate(to_enrich)
    )

    try:
        resp = await client.chat.completions.create(
            model=HYDE_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _HYDE_SYSTEM},
                {"role": "user",   "content": numbered},
            ],
        )
        raw  = json.loads(resp.choices[0].message.content or "[]")
        # The model may return {"chunks": [...]} or just [...]
        items: list = raw if isinstance(raw, list) else (
            raw.get("chunks") or raw.get("results") or list(raw.values())[0]
            if isinstance(raw, dict) else []
        )
        for i, item in enumerate(items):
            if i >= len(to_enrich):
                break
            qs = item.get("q", []) if isinstance(item, dict) else []
            to_enrich[i].hypothetical_questions = [q for q in qs if isinstance(q, str)]
    except Exception as exc:
        logger.warning("hyde enrichment failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

_PARSERS = {
    "github":  _parse_markdown,
    "jira":    _parse_jira,
    "slack":   _parse_slack,
    "hubspot": _parse_markdown,
}


async def chunk_document(
    content: str,
    source_type: str,
    metadata: dict | None = None,
) -> list[StructuredChunk]:
    """
    Main entry point.

    1. Parses the document into a structural tree (headings, code, tables, …)
    2. Splits into token-bounded chunks respecting structural boundaries
    3. Enriches every chunk with HyDE hypothetical questions (one batched call)

    Returns a list of enriched StructuredChunk objects ready for embedding.
    """
    md      = metadata or {}
    parser  = _PARSERS.get(source_type, _parse_markdown)
    sections = parser(content)

    chunks = _sections_to_chunks(sections, source_type, md)

    if not chunks:
        logger.warning("chunker: no chunks produced for source=%s len=%d", source_type, len(content))
        return []

    # Always enrich — this is what makes retrieval precise
    await _enrich_batch(chunks)

    logger.info(
        "chunker v2: source=%s sections=%d chunks=%d (hyde=%s)",
        source_type, len(sections), len(chunks),
        "ok" if any(c.hypothetical_questions for c in chunks) else "empty",
    )
    return chunks
