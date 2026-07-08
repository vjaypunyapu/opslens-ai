"""
OpsLens AI — Human-Thought Validation Nodes
=============================================
Three LLM-powered "inner critic" nodes that mirror how a careful human
analyst would review an answer before publishing it.

  Auditor      — Grounding check.  Does every claim in the answer have
                 explicit support in the retrieved context?  Flags
                 hallucinations and unsupported statements.

  Gatekeeper   — Completeness check.  Did the answer fully address the
                 original question?  Identifies missing sub-questions and
                 gaps so the planner can issue follow-up retrievals.

  Strategist   — Logic & coherence check.  Are the conclusions drawn from
                 the evidence sound?  Catches contradictions, circular
                 reasoning, and over-generalisation.

Each node returns a structured ValidationResult.  If all three pass,
the answer is considered safe to stream to the user.  If any node raises
a concern, the planner can choose to re-retrieve, refine, or add a
disclaimer.

Usage (standalone):
    result = await validate_answer(question, context_docs, answer)
    if result.passed:
        stream answer …
    else:
        handle result.issues …

These nodes are cheap (gpt-4o-mini, ~$0.001 per call) and fast (~300 ms).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document

from ..config import settings
from ..utils.logging import get_logger
from . import telemetry

logger = get_logger(__name__)

# ── LLM helpers ───────────────────────────────────────────────────────────────
def _openai_key() -> str:
    return (
        os.environ.get("OPENAI_TOKEN", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or settings.OPENAI_API_KEY
        or ""
    ).strip()


def _langsmith_client():
    """Return a LangSmith Client if tracing is enabled, else None."""
    if not settings.LANGCHAIN_TRACING_V2 or not settings.LANGCHAIN_API_KEY:
        return None
    try:
        from langsmith import Client
        return Client(api_key=settings.LANGCHAIN_API_KEY)
    except Exception:
        return None


async def _call_validator(
    step: str,
    system_prompt: str,
    user_content: str,
) -> dict[str, Any]:
    """
    Shared helper: calls GPT-4o-mini with a system prompt and returns parsed JSON.
    Records a telemetry span and (optionally) a LangSmith run for the call.
    Falls back gracefully on any error.
    """
    import time
    import uuid as _uuid
    t0 = time.monotonic()
    model = "gpt-4o-mini"
    in_tok = out_tok = 0
    ls = _langsmith_client()
    run_id = str(_uuid.uuid4()) if ls else None

    # Open LangSmith run
    if ls and run_id:
        try:
            ls.create_run(
                id=run_id,
                name=f"validator/{step}",
                run_type="llm",
                project_name=settings.LANGCHAIN_PROJECT,
                inputs={"system": system_prompt, "user": user_content},
            )
        except Exception as exc:
            logger.debug("langsmith create_run failed: %s", exc)
            run_id = None

    result: dict[str, Any] = {}
    error: str | None = None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_openai_key())
        resp = await client.chat.completions.create(
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
        )
        in_tok  = resp.usage.prompt_tokens     if resp.usage else 0
        out_tok = resp.usage.completion_tokens if resp.usage else 0
        result = json.loads(resp.choices[0].message.content or "{}")
        return result
    except Exception as exc:
        logger.warning("validator LLM call failed (%s): %s", step, exc)
        error = str(exc)
        return {}
    finally:
        latency = (time.monotonic() - t0) * 1000
        telemetry.record_llm_span(step, model, in_tok, out_tok, latency_ms=latency)

        # Close LangSmith run
        if ls and run_id:
            try:
                ls.update_run(
                    run_id,
                    outputs=result,
                    error=error,
                    end_time=None,  # auto
                    extra={"tokens": {"input": in_tok, "output": out_tok}},
                )
            except Exception as exc:
                logger.debug("langsmith update_run failed: %s", exc)


# ── Result type ───────────────────────────────────────────────────────────────
@dataclass
class ValidationResult:
    node: str                               # "auditor" | "gatekeeper" | "strategist"
    passed: bool                            # True = no blocking issues
    score: float                            # 0.0–1.0 confidence
    issues: list[str] = field(default_factory=list)   # human-readable concerns
    suggestions: list[str] = field(default_factory=list)  # how to improve
    raw: dict[str, Any] = field(default_factory=dict)     # full LLM output


@dataclass
class FullValidation:
    auditor:    ValidationResult
    gatekeeper: ValidationResult
    strategist: ValidationResult

    @property
    def passed(self) -> bool:
        return self.auditor.passed and self.gatekeeper.passed and self.strategist.passed

    @property
    def overall_score(self) -> float:
        return (self.auditor.score + self.gatekeeper.score + self.strategist.score) / 3.0

    def blocking_issues(self) -> list[str]:
        issues: list[str] = []
        for node in (self.auditor, self.gatekeeper, self.strategist):
            if not node.passed:
                issues.extend(f"[{node.node.upper()}] {i}" for i in node.issues)
        return issues

    def disclaimer(self) -> str | None:
        """Return a caveat only when the Auditor detects hallucination.

        Gatekeeper/Strategist failures are advisory — completeness concerns
        (e.g. 'maybe there are more PRs') should not show a warning to the
        user when the Auditor confirms every claim is grounded in context.
        """
        if self.auditor.passed:
            return None
        return (
            "⚠️ *Note: this answer may be incomplete or unverified in places. "
            "Please cross-check with your source systems.*"
        )


# ── Node: Auditor (hallucination / grounding) ─────────────────────────────────
_AUDITOR_SYSTEM = """\
You are an expert fact-checker called the Auditor.
Given a question, a set of retrieved context passages, and a generated answer,
your job is to determine whether every factual claim in the answer is explicitly
supported by the context passages.

Output JSON with exactly these keys:
  "passed":      boolean  — true if no unsupported claims found
  "score":       float    — 0.0 (many hallucinations) to 1.0 (fully grounded)
  "issues":      list of strings — each unsupported or contradicted claim
  "suggestions": list of strings — how to fix each issue

Be strict: if a claim cannot be traced to the context, flag it.
If the answer says "I don't have enough information", that is acceptable — passed=true.
Output raw JSON only, no markdown."""


async def run_auditor(
    question: str,
    context_docs: list[Document],
    answer: str,
) -> ValidationResult:
    context_text = "\n\n---\n\n".join(
        f"[{i+1}] {d.page_content[:400]}" for i, d in enumerate(context_docs[:8])
    )
    user_content = (
        f"QUESTION:\n{question}\n\n"
        f"CONTEXT PASSAGES:\n{context_text}\n\n"
        f"GENERATED ANSWER:\n{answer}"
    )
    raw = await _call_validator("auditor", _AUDITOR_SYSTEM, user_content)
    return ValidationResult(
        node="auditor",
        passed=bool(raw.get("passed", True)),
        score=float(raw.get("score", 1.0)),
        issues=raw.get("issues", []),
        suggestions=raw.get("suggestions", []),
        raw=raw,
    )


# ── Node: Gatekeeper (completeness) ──────────────────────────────────────────
_GATEKEEPER_SYSTEM = """\
You are an expert reviewer called the Gatekeeper.
Given a question, the retrieved context passages the answer was based on, and a
generated answer, determine whether the answer fully addresses the question
GIVEN THE AVAILABLE CONTEXT.

Important: evaluate completeness relative to what the context contains, not
relative to what might theoretically exist. If the context has one PR and the
answer describes it, that is complete — do not flag "maybe there are more PRs."

Output JSON with exactly these keys:
  "passed":          boolean — true if the answer fully addresses the question given the context
  "score":           float   — 0.0 (totally incomplete) to 1.0 (fully complete)
  "issues":          list of strings — aspects of the question the context could answer but the answer skipped
  "missing_queries": list of strings — additional search queries that could fill genuine gaps
  "suggestions":     list of strings — improvements to the answer

Output raw JSON only, no markdown."""


async def run_gatekeeper(
    question: str,
    answer: str,
    context_docs: list[Document] | None = None,
) -> ValidationResult:
    context_text = ""
    if context_docs:
        context_text = "\n\n---\n\n".join(
            f"[{i+1}] {d.page_content[:300]}" for i, d in enumerate(context_docs[:8])
        )
    user_content = (
        f"QUESTION:\n{question}\n\n"
        + (f"RETRIEVED CONTEXT:\n{context_text}\n\n" if context_text else "")
        + f"GENERATED ANSWER:\n{answer}"
    )
    raw = await _call_validator("gatekeeper", _GATEKEEPER_SYSTEM, user_content)
    result = ValidationResult(
        node="gatekeeper",
        passed=bool(raw.get("passed", True)),
        score=float(raw.get("score", 1.0)),
        issues=raw.get("issues", []),
        suggestions=raw.get("suggestions", []),
        raw=raw,
    )
    # Attach missing_queries as extra so the planner can use them
    result.raw["missing_queries"] = raw.get("missing_queries", [])
    return result


# ── Node: Strategist (logic & coherence) ─────────────────────────────────────
_STRATEGIST_SYSTEM = """\
You are an expert analyst called the Strategist.
Given a question and a generated answer, your job is to evaluate whether the
reasoning and conclusions in the answer are logically sound and coherent.
Look for: circular reasoning, unsound inferences, contradictions, over-generalisation,
and conclusions that don't follow from the evidence.

Output JSON with exactly these keys:
  "passed":      boolean — true if the reasoning is sound
  "score":       float   — 0.0 (seriously flawed logic) to 1.0 (sound reasoning)
  "issues":      list of strings — specific logical flaws found
  "suggestions": list of strings — how to improve the reasoning

Output raw JSON only, no markdown."""


async def run_strategist(
    question: str,
    answer: str,
) -> ValidationResult:
    user_content = f"QUESTION:\n{question}\n\nGENERATED ANSWER:\n{answer}"
    raw = await _call_validator("strategist", _STRATEGIST_SYSTEM, user_content)
    return ValidationResult(
        node="strategist",
        passed=bool(raw.get("passed", True)),
        score=float(raw.get("score", 1.0)),
        issues=raw.get("issues", []),
        suggestions=raw.get("suggestions", []),
        raw=raw,
    )


# ── Public API ────────────────────────────────────────────────────────────────
async def validate_answer(
    question: str,
    context_docs: list[Document],
    answer: str,
) -> FullValidation:
    """
    Run all three validation nodes in parallel and return a FullValidation.
    Each node is independent and can be run concurrently.
    """
    import asyncio

    auditor_task    = asyncio.create_task(run_auditor(question, context_docs, answer))
    gatekeeper_task = asyncio.create_task(run_gatekeeper(question, answer, context_docs))
    strategist_task = asyncio.create_task(run_strategist(question, answer))

    auditor_result, gatekeeper_result, strategist_result = await asyncio.gather(
        auditor_task, gatekeeper_task, strategist_task, return_exceptions=False
    )

    validation = FullValidation(
        auditor=auditor_result,
        gatekeeper=gatekeeper_result,
        strategist=strategist_result,
    )
    logger.info(
        "validate_answer: passed=%s scores=A:%.2f G:%.2f S:%.2f",
        validation.passed,
        validation.auditor.score,
        validation.gatekeeper.score,
        validation.strategist.score,
    )
    return validation
