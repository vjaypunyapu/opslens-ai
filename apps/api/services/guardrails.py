"""
OpsLens AI — Input/Output Guardrails
======================================
Lightweight, dependency-free guardrail layer that runs on every user question
and every LLM answer.

Input guards (applied before any LLM call):
  1. Length cap           — truncates questions exceeding INPUT_MAX_CHARS
  2. Prompt injection     — detects adversarial instruction patterns and blocks
  3. Jailbreak heuristics — catches role-switching / DAN-style attacks
  4. Topic scope          — rejects questions clearly outside OpsLens's domain
                            (entertainment, recipes, general knowledge, etc.)

Output guards (applied to the final answer before streaming):
  1. PII redaction        — masks emails, phone numbers, credit card numbers,
                            SSNs, and AWS-style secrets with [REDACTED-<type>]
  2. Content filtering    — blocks answers that contain no useful signal

All functions are synchronous and fast (regex-only, no LLM calls).
The LLM-level topic check is a second layer inside supervisor_node.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    allowed: bool
    sanitized: str          # input/output after safe transforms
    blocked_reason: str = ""  # non-empty when allowed=False


# ── Prompt injection patterns ──────────────────────────────────────────────────
# Regex patterns that strongly indicate adversarial prompt manipulation.
# Each is case-insensitive. A match means the request is BLOCKED.

_INJECTION_PATTERNS: list[re.Pattern] = [
    # Classic instruction override
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|context)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?)", re.I),
    re.compile(r"forget\s+(everything|all|what)\s+(you('ve| have))?\s*(been\s+told|learned|know)", re.I),

    # Role/persona hijacking
    re.compile(r"\bact\s+as\s+(if\s+you\s+are|a|an)\s+.{0,40}(without\s+(restriction|limit|filter|safeguard|ethic|moral|censor))", re.I),
    re.compile(r"\bpretend\s+(you\s+are|to\s+be)\s+.{0,40}(unrestricted|evil|jailbreak|DAN|no\s+limit)", re.I),
    re.compile(r"\byou\s+are\s+now\s+.{0,40}(DAN|unrestricted|jailbroken|free\s+AI)", re.I),

    # DAN / jailbreak keywords
    re.compile(r"\b(DAN|do\s+anything\s+now|jailbreak(ed)?|no\s+restrictions?\s+mode)\b", re.I),

    # System prompt leakage
    re.compile(r"(print|reveal|show|output|repeat|tell me|what is)\s+(your\s+)?(system\s+prompt|initial\s+prompt|instructions?\s+given|secret\s+instructions?)", re.I),

    # Indirect injection via "the document says" override
    re.compile(r"the\s+(document|context|file|text)\s+says?[:\s]+ignore\s+(all\s+)?previous", re.I),
]

# ── Jailbreak soft signals ────────────────────────────────────────────────────
# These don't immediately block but are logged as warnings. If 3+ match, block.

_JAILBREAK_SOFT: list[re.Pattern] = [
    re.compile(r"\bno\s+(ethical|moral|safety)\s+(guidelines?|filters?|constraint)", re.I),
    re.compile(r"\bbypass\s+(filter|safeguard|content\s+policy|restriction|guardrail)", re.I),
    re.compile(r"\bsuperhero\s+mode\b|\bdeveloper\s+mode\b|\bgod\s+mode\b", re.I),
    re.compile(r"\bunlimited\s+(power|access|capability|mode)\b", re.I),
    re.compile(r"\bdo\s+not\s+(refuse|decline|say\s+no)\b", re.I),
]

# ── PII patterns ──────────────────────────────────────────────────────────────

_PII_RULES: list[tuple[str, re.Pattern]] = [
    # Email addresses
    ("EMAIL",       re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # US phone numbers (various formats)
    ("PHONE",       re.compile(r"\b(\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")),
    # Credit card numbers (13–19 digits, optional separators)
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    # US SSN
    ("SSN",         re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")),
    # AWS access key ID (starts with AKIA / ASIA)
    ("AWS_KEY",     re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
    # Generic API key pattern (≥32 hex/base64 chars after common prefixes)
    ("API_KEY",     re.compile(r"\b(sk|pk|api|key|secret|token)[_\-][A-Za-z0-9_\-]{32,}\b", re.I)),
    # IPv4 addresses (only if private — public IPs in logs are acceptable)
    # We redact private ranges: 10.x, 172.16-31.x, 192.168.x
    ("PRIVATE_IP",  re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b")),
]

# ── Off-topic / out-of-scope patterns ────────────────────────────────────────
# These match questions that are clearly outside OpsLens's domain.
# Organised by category. Any single match blocks the question.
#
# OpsLens's domain: software operations, engineering, incidents, alerts,
# Jira/GitHub/Slack data, logs, deployments, product analytics, customer support
# metrics, infra/cloud, team productivity.

_OUT_OF_SCOPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Entertainment & media
    ("entertainment", re.compile(
        r"\b(netflix|hulu|disney\+?|prime\s+video|hbo|spotify|youtube|twitch)\b.{0,60}"
        r"(recommend|suggest|watch|listen|best|top|good)", re.I)),
    ("movie_tv",      re.compile(
        r"\b(movie|film|tv\s+show|series|episode|season|anime|documentary|trailer)"
        r"\s+(to\s+)?(watch|recommend|suggest|see|stream|buy)\b", re.I)),
    ("music",         re.compile(
        r"\b(song|playlist|album|artist|band|music)\s+(to\s+)?(listen|recommend|suggest|play)\b", re.I)),

    # Food & lifestyle
    ("recipe",        re.compile(
        r"\b(recipe|ingredient|cook|bake|dish|meal|food|restaurant|cuisin)\b", re.I)),
    ("travel",        re.compile(
        r"\b(travel|vacation|holiday|hotel|flight|tourist|destination|visa|passport)\b", re.I)),
    ("fashion",       re.compile(
        r"\b(fashion|outfit|cloth(es|ing)|dress|shoe|wear|style\s+advice)\b", re.I)),

    # Health & wellness
    ("medical",       re.compile(
        r"\b(doctor|symptom|diagnos|medication|pill|drug|disease|health\s+advice"
        r"|calor(ie|y)|diet|weight\s+loss|workout\s+plan)\b", re.I)),

    # Finance & personal
    ("personal_finance", re.compile(
        r"\b(stock\s+pick|crypto\s+(buy|invest)|bitcoin\s+price|invest(ment)?\s+advice"
        r"|real\s+estate|mortgage\s+rate|lottery)\b", re.I)),

    # General trivia / homework
    ("trivia",        re.compile(
        r"\b(who\s+is\s+(the\s+)?president|capital\s+of\s+\w+|history\s+of\s+\w+\s+war"
        r"|write\s+(me\s+)?(a\s+)?(poem|essay|story|joke|rap)|tell\s+me\s+a\s+(joke|story|fun\s+fact))\b", re.I)),

    # Sports
    ("sports",        re.compile(
        r"\b(nfl|nba|mlb|nhl|soccer|football\s+score|match\s+result|who\s+won\s+the\s+"
        r"(game|match|tournament))\b", re.I)),

    # Dating / social
    ("dating",        re.compile(
        r"\b(date\s+(idea|advice)|relationship\s+advice|tinder|bumble|how\s+to\s+(impress|flirt))\b", re.I)),
]

_OUT_OF_SCOPE_MESSAGE = (
    "I'm OpsLens AI — I can only help with questions about your engineering operations, "
    "incidents, alerts, deployments, Jira tickets, Slack messages, GitHub activity, "
    "logs, and related operational data. Please ask something related to your team's work."
)

# ── Content filter ────────────────────────────────────────────────────────────
# Block outbound answers that are essentially empty or contain only boilerplate.

_EMPTY_ANSWER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*$"),
    re.compile(r"^(\[Error generating response.*?\])\s*$", re.I | re.S),
]


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════

def check_topic_scope(question: str) -> GuardrailResult:
    """
    Reject questions that are clearly outside OpsLens's operational domain.

    This is a fast regex pre-filter. The supervisor LLM performs a second,
    more nuanced scope check for ambiguous cases.
    """
    for category, pattern in _OUT_OF_SCOPE_PATTERNS:
        if pattern.search(question):
            logger.info(
                "guardrails: out-of-scope question blocked (category=%s)", category
            )
            return GuardrailResult(
                allowed=False,
                sanitized=question,
                blocked_reason=_OUT_OF_SCOPE_MESSAGE,
            )
    return GuardrailResult(allowed=True, sanitized=question)


def check_input(question: str) -> GuardrailResult:
    """
    Validate and sanitise a raw user question before it reaches any LLM.

    Steps applied (in order):
      1. Length cap — truncate to INPUT_MAX_CHARS
      2. Hard injection block — match against known adversarial patterns
      3. Soft jailbreak heuristics — block if ≥3 soft signals match
      4. Topic scope — reject clearly off-topic questions
    """
    max_chars = settings.INPUT_MAX_CHARS

    # 1. Length cap
    if len(question) > max_chars:
        question = question[:max_chars]
        logger.warning("guardrails: input truncated to %d chars", max_chars)

    # 2. Hard injection block
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(question):
            logger.warning(
                "guardrails: prompt injection detected (pattern=%r)", pattern.pattern[:60]
            )
            return GuardrailResult(
                allowed=False,
                sanitized=question,
                blocked_reason=(
                    "Your message contains patterns that look like attempts to override "
                    "system instructions. Please rephrase your question."
                ),
            )

    # 3. Soft jailbreak heuristics
    soft_hits = sum(1 for p in _JAILBREAK_SOFT if p.search(question))
    if soft_hits >= 3:
        logger.warning("guardrails: jailbreak heuristic triggered (%d soft signals)", soft_hits)
        return GuardrailResult(
            allowed=False,
            sanitized=question,
            blocked_reason=(
                "Your message contains content that cannot be processed. "
                "Please ask a question about your operational data."
            ),
        )

    # 4. Topic scope (fast regex pass — supervisor does a second LLM-level check)
    scope = check_topic_scope(question)
    if not scope.allowed:
        return scope

    return GuardrailResult(allowed=True, sanitized=question)


def redact_output(answer: str) -> str:
    """
    Apply PII redaction to the final LLM answer before streaming to the user.

    Replaces matched PII with [REDACTED-<TYPE>] placeholders.
    Skips redaction inside code blocks (``` fenced blocks) to avoid
    mangling example credentials that are already obviously redacted by context.
    """
    if not answer:
        return answer

    # Split on fenced code blocks — only redact prose sections
    # Pattern: alternate prose/code segments
    parts = re.split(r"(```[\s\S]*?```)", answer)
    redacted_parts: list[str] = []

    for i, part in enumerate(parts):
        if part.startswith("```"):
            # Code block — leave as-is
            redacted_parts.append(part)
        else:
            # Prose — apply PII rules
            for label, pattern in _PII_RULES:
                part = pattern.sub(f"[REDACTED-{label}]", part)
            redacted_parts.append(part)

    result = "".join(redacted_parts)

    if result != answer:
        logger.info("guardrails: PII redacted from output")

    return result


def check_output(answer: str) -> GuardrailResult:
    """
    Validate the final answer before streaming.

    Returns allowed=False for empty or pure-error answers so the caller
    can substitute a generic error message instead of streaming nothing.
    """
    for pattern in _EMPTY_ANSWER_PATTERNS:
        if pattern.match(answer.strip()):
            return GuardrailResult(
                allowed=False,
                sanitized=answer,
                blocked_reason="The system could not generate a useful response. Please try again.",
            )

    redacted = redact_output(answer)
    return GuardrailResult(allowed=True, sanitized=redacted)
