"""
OpsLens AI — Structural Document Parser
=========================================
Replaces the naive 512-token sliding-window chunker with a structure-aware
parser that respects document semantics before chunking.

Rules (applied in this priority order):
  1. Fenced code blocks  (``` ... ```)  → always ONE atomic chunk, never split.
  2. HTML/Markdown tables               → always ONE atomic chunk, never split.
  3. Heading boundaries  (# / ## / etc) → flush current text buffer; every
     subsequent chunk inherits the heading path ("Parent > Child") prepended
     to its content and stored separately in metadata.
  4. Regular prose / list items         → sliding-window token chunks
     (CHUNK_TOKENS=512, OVERLAP_TOKENS=50) within the current section.

Output: list[StructuralChunk]

Each StructuralChunk carries:
  .content     — text to embed (heading path already prepended for retrieval)
  .raw_content — original text without the heading prefix
  .heading     — nearest-parent heading text, e.g. "Authentication > OAuth Flow"
  .chunk_type  — "text" | "code" | "table"
  .metadata    — {"code_language": str, "heading_level": int, ...}

HyDE (Hypothetical Document Embeddings):
  After structural chunking, call generate_hyde_questions(chunks) to augment
  each chunk's content with 1-3 hypothetical questions that the chunk answers.
  This dramatically improves recall for question-style user queries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import tiktoken

# ── Config ────────────────────────────────────────────────────────────────────
CHUNK_TOKENS   = 512    # soft token limit for prose sections
OVERLAP_TOKENS = 50     # token overlap between adjacent prose chunks
MAX_ATOMIC_WARN = 4096  # warn (but don't split) atomic blocks above this size

_enc = tiktoken.get_encoding("cl100k_base")


# ── Data model ────────────────────────────────────────────────────────────────
@dataclass
class StructuralChunk:
    content: str                              # full text sent to embedding (heading prepended)
    raw_content: str                          # original text without heading prefix
    heading: str = ""                         # breadcrumb, e.g. "Auth > OAuth Flow"
    chunk_type: str = "text"                  # "text" | "code" | "table"
    metadata: dict[str, Any] = field(default_factory=dict)
    # populated later by generate_hyde_questions()
    hyde_questions: list[str] = field(default_factory=list)


# ── Heading utilities ─────────────────────────────────────────────────────────
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_RE   = re.compile(r"^(`{3,}|~{3,})([\w\-+#.]*)\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_HTML_TABLE_OPEN  = re.compile(r"<table[\s>]", re.IGNORECASE)
_HTML_TABLE_CLOSE = re.compile(r"</table>", re.IGNORECASE)


def _heading_path(stack: list[tuple[int, str]]) -> str:
    """Format heading stack as breadcrumb: 'Parent > Child'."""
    return " > ".join(text for _, text in stack)


def _update_heading_stack(
    stack: list[tuple[int, str]],
    level: int,
    text: str,
) -> list[tuple[int, str]]:
    """Return a new stack with the heading inserted at the correct level."""
    # Pop headings at the same or deeper level
    trimmed = [(lvl, txt) for lvl, txt in stack if lvl < level]
    trimmed.append((level, text.strip()))
    return trimmed


# ── Prose chunker (token-bounded, within a section) ──────────────────────────
def _prose_chunks(
    text: str,
    heading: str,
) -> list[StructuralChunk]:
    """Split prose text into token-bounded chunks with overlap."""
    text = text.strip()
    if not text:
        return []

    tokens = _enc.encode(text)
    if not tokens:
        return []

    chunks: list[StructuralChunk] = []
    i = 0
    while i < len(tokens):
        window = tokens[i: i + CHUNK_TOKENS]
        raw = _enc.decode(window).strip()
        if not raw:
            i += CHUNK_TOKENS - OVERLAP_TOKENS
            continue

        # Prepend heading so the embedding captures section context
        full = f"[{heading}]\n\n{raw}" if heading else raw
        chunks.append(StructuralChunk(
            content=full,
            raw_content=raw,
            heading=heading,
            chunk_type="text",
        ))
        i += CHUNK_TOKENS - OVERLAP_TOKENS

    return chunks


# ── Public API ────────────────────────────────────────────────────────────────
def structural_parse(text: str) -> list[StructuralChunk]:
    """
    Parse `text` into a list of StructuralChunks respecting document structure.

    Handles:
    - Fenced code blocks (``` / ~~~) — kept atomic
    - Markdown tables (|col|col|) — kept atomic
    - HTML <table>...</table> — kept atomic
    - ATX headings (# ## ###) — flush buffer, update heading breadcrumb
    - Regular prose — 512-token sliding window within the current section
    """
    lines = text.splitlines()
    chunks: list[StructuralChunk] = []
    heading_stack: list[tuple[int, str]] = []
    prose_buffer: list[str] = []

    def _flush_prose() -> None:
        """Emit prose_buffer as token-bounded chunks, then clear it."""
        if not prose_buffer:
            return
        prose_text = "\n".join(prose_buffer).strip()
        heading = _heading_path(heading_stack)
        chunks.extend(_prose_chunks(prose_text, heading))
        prose_buffer.clear()

    i = 0
    while i < len(lines):
        line = lines[i]

        # ── Fenced code block ─────────────────────────────────────────────────
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            _flush_prose()
            fence_char = fence_match.group(1)       # ``` or ~~~
            lang       = fence_match.group(2) or "" # python, js, ...
            code_lines = []
            i += 1
            while i < len(lines):
                if lines[i].startswith(fence_char):
                    i += 1  # consume closing fence
                    break
                code_lines.append(lines[i])
                i += 1

            raw_code = "\n".join(code_lines)
            heading  = _heading_path(heading_stack)
            tokens   = len(_enc.encode(raw_code))
            if tokens > MAX_ATOMIC_WARN:
                pass  # kept whole per spec — just don't warn in prod
            prefix = f"[{heading}]\n\n" if heading else ""
            lang_hint = f"```{lang}\n" if lang else "```\n"
            full_code = f"{prefix}{lang_hint}{raw_code}\n```"
            chunks.append(StructuralChunk(
                content=full_code,
                raw_content=raw_code,
                heading=heading,
                chunk_type="code",
                metadata={"code_language": lang},
            ))
            continue

        # ── HTML table ────────────────────────────────────────────────────────
        if _HTML_TABLE_OPEN.search(line):
            _flush_prose()
            table_lines = [line]
            i += 1
            while i < len(lines):
                table_lines.append(lines[i])
                if _HTML_TABLE_CLOSE.search(lines[i]):
                    i += 1
                    break
                i += 1
            raw_table = "\n".join(table_lines)
            heading   = _heading_path(heading_stack)
            prefix    = f"[{heading}]\n\n" if heading else ""
            chunks.append(StructuralChunk(
                content=f"{prefix}{raw_table}",
                raw_content=raw_table,
                heading=heading,
                chunk_type="table",
                metadata={"table_format": "html"},
            ))
            continue

        # ── Markdown table ────────────────────────────────────────────────────
        if _TABLE_ROW_RE.match(line):
            _flush_prose()
            table_lines = [line]
            i += 1
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            # Must have at least a header + separator to be a real MD table
            if len(table_lines) >= 2:
                raw_table = "\n".join(table_lines)
                heading   = _heading_path(heading_stack)
                prefix    = f"[{heading}]\n\n" if heading else ""
                chunks.append(StructuralChunk(
                    content=f"{prefix}{raw_table}",
                    raw_content=raw_table,
                    heading=heading,
                    chunk_type="table",
                    metadata={"table_format": "markdown"},
                ))
            else:
                # Single | line — treat as prose
                prose_buffer.extend(table_lines)
            continue

        # ── ATX heading ───────────────────────────────────────────────────────
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            _flush_prose()
            level = len(heading_match.group(1))
            text_  = heading_match.group(2)
            heading_stack = _update_heading_stack(heading_stack, level, text_)
            # Don't add the heading line itself as a chunk — it becomes the
            # breadcrumb prefix on all subsequent chunks in this section.
            i += 1
            continue

        # ── Regular prose line ────────────────────────────────────────────────
        prose_buffer.append(line)
        i += 1

    # Flush any remaining prose
    _flush_prose()

    # Drop empty chunks that slipped through (blank sections, etc.)
    return [c for c in chunks if c.content.strip()]


# ── HyDE: Hypothetical Document Embeddings ────────────────────────────────────
_HYDE_SYSTEM = """\
You are a question-generation assistant. For each numbered content chunk below,
write 1-3 specific, diverse questions that ONLY that chunk directly answers.

Rules:
- Mix question types: factual ("what is X"), procedural ("how do I Y"),
  reasoning ("why does Z happen"), and diagnostic ("what causes W").
- Keep each question under 20 words.
- For pure code blocks or table rows with no prose context, write 1 question
  about what the code/table demonstrates or how to use it.
- Do NOT repeat questions across chunks.

Respond with raw JSON only — no markdown, no explanation:
{"results": [["question1", "question2"], ["question1"], ...]}

The array must have exactly one sub-array per chunk, in the same order.
"""


async def generate_hyde_questions(
    chunks: list[StructuralChunk],
    openai_client: Any,
    batch_size: int = 40,
) -> list[StructuralChunk]:
    """
    Augment each chunk with 1-3 hypothetical questions via gpt-4o-mini.

    Sends all chunks for a document in batched API calls (batch_size per call)
    so we pay ~1 API call per document, not 1 per chunk.

    Mutates chunks in-place (appends questions to .hyde_questions and appends
    the question text to .content so they are embedded alongside the chunk).
    Returns the same list for chaining.
    """
    import json

    if not chunks:
        return chunks

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start: batch_start + batch_size]

        # Build the user message listing each chunk
        parts = []
        for idx, chunk in enumerate(batch, start=1):
            # Use raw_content (without heading prefix) so the LLM sees clean text
            preview = chunk.raw_content[:600].strip()
            parts.append(f"CHUNK {idx} [{chunk.chunk_type.upper()}]:\n{preview}")
        user_msg = "\n\n---\n\n".join(parts)

        try:
            resp = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.4,
                max_tokens=1024,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _HYDE_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
            )
            raw = json.loads(resp.choices[0].message.content or "{}")
            results: list[list[str]] = raw.get("results", [])
        except Exception as exc:
            # HyDE is best-effort — never block ingestion on failure
            import logging
            logging.getLogger(__name__).warning("HyDE batch failed: %s", exc)
            results = []

        for local_i, chunk in enumerate(batch):
            questions: list[str] = []
            if local_i < len(results) and isinstance(results[local_i], list):
                questions = [q for q in results[local_i] if isinstance(q, str) and q.strip()][:3]

            if questions:
                chunk.hyde_questions = questions
                # Append questions to content so they are co-embedded with the chunk
                q_block = "\n".join(f"Q: {q}" for q in questions)
                chunk.content = f"{chunk.content}\n\n{q_block}"

    return chunks
