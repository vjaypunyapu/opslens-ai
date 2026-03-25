"""
OpsLens AI — Performance Telemetry
=====================================
Tracks every LLM call, retrieval step, and validation loop for a single
user query under a unified trace_id.  Uses Python contextvars so the
trace_id propagates automatically through the async call-stack — no
function signature changes required in the instrumented modules.

Trace lifecycle:
  rag_service.stream()
    └─ telemetry.start_trace(trace_id, tenant_id, question)   ← sets ContextVar
         ├─ planner._plan_node()         → record_llm_span("planner", ...)
         ├─ hybrid_retriever()           → record_latency_span("retrieval", ...)
         ├─ planner._generate_node()     → record_llm_span("generation", ...)
         ├─ validator.run_auditor()      → record_llm_span("auditor", ...)
         ├─ validator.run_gatekeeper()   → record_llm_span("gatekeeper", ...)
         ├─ validator.run_strategist()   → record_llm_span("strategist", ...)
         └─ telemetry.finish_trace(...)  ← seals the trace, stores it

Model pricing (USD per 1 000 tokens, as of 2025-06):
  gpt-4o          $0.00250 input  /  $0.01000 output
  gpt-4o-mini     $0.00015 input  /  $0.00060 output
  text-embedding-3-small   $0.000020 per 1k tokens (input only)
  cohere rerank-v3         ~$0.001 per 1k docs  (approximated)
"""
from __future__ import annotations

import contextvars
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..utils.logging import get_logger

logger = get_logger(__name__)

# ── Context propagation ────────────────────────────────────────────────────────
# Setting this ContextVar at the start of a request means every awaited
# coroutine in the same async task automatically inherits the trace_id.
_current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "opslens_trace_id", default=""
)


def current_trace_id() -> str:
    return _current_trace_id.get()


# ── Model pricing table (USD / 1k tokens) ─────────────────────────────────────
_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":                    {"input": 0.00250, "output": 0.01000},
    "gpt-4o-2024-11-20":         {"input": 0.00250, "output": 0.01000},
    "gpt-4o-mini":               {"input": 0.00015, "output": 0.00060},
    "gpt-4o-mini-2024-07-18":    {"input": 0.00015, "output": 0.00060},
    "text-embedding-3-small":    {"input": 0.000020, "output": 0.0},
    "text-embedding-3-large":    {"input": 0.000130, "output": 0.0},
    "claude-3-5-sonnet-20241022":{"input": 0.00300, "output": 0.01500},
    "claude-3-haiku-20240307":   {"input": 0.00025, "output": 0.00125},
    "cohere-rerank":             {"input": 0.001,   "output": 0.0},   # per 1k docs approximation
}


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _PRICING.get(model, {"input": 0.0, "output": 0.0})
    return (input_tokens / 1000) * p["input"] + (output_tokens / 1000) * p["output"]


# ── Span ──────────────────────────────────────────────────────────────────────
@dataclass
class Span:
    """One instrumented step within a trace."""
    step:          str     # "planner" | "retrieval" | "generation" | "auditor" |
                           # "gatekeeper" | "strategist" | "hyde" | "embedding"
    model:         str     = ""
    input_tokens:  int     = 0
    output_tokens: int     = 0
    latency_ms:    float   = 0.0
    cost_usd:      float   = 0.0
    metadata:      dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.cost_usd == 0.0 and self.model:
            self.cost_usd = _cost_usd(self.model, self.input_tokens, self.output_tokens)


# ── Trace ─────────────────────────────────────────────────────────────────────
@dataclass
class Trace:
    """Complete record of a single user query lifecycle."""
    trace_id:          str
    tenant_id:         str
    question:          str
    started_at:        float   = field(default_factory=time.monotonic)
    ended_at:          float   = 0.0
    spans:             list[Span] = field(default_factory=list)
    retry_count:       int     = 0
    validation_passed: bool    = True
    error:             str     = ""

    # ── Aggregates ────────────────────────────────────────────────────────────
    @property
    def total_latency_ms(self) -> float:
        if self.ended_at:
            return (self.ended_at - self.started_at) * 1000
        return sum(s.latency_ms for s in self.spans)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.spans)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.spans)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.spans)

    def span_by_step(self, step: str) -> Span | None:
        return next((s for s in self.spans if s.step == step), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id":          self.trace_id,
            "tenant_id":         self.tenant_id,
            "question_preview":  self.question[:120],
            "total_latency_ms":  round(self.total_latency_ms, 1),
            "total_cost_usd":    round(self.total_cost_usd, 6),
            "total_tokens":      self.total_tokens,
            "retry_count":       self.retry_count,
            "validation_passed": self.validation_passed,
            "error":             self.error,
            "spans": [
                {
                    "step":          s.step,
                    "model":         s.model,
                    "input_tokens":  s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "latency_ms":    round(s.latency_ms, 1),
                    "cost_usd":      round(s.cost_usd, 6),
                    **({k: v for k, v in s.metadata.items()} if s.metadata else {}),
                }
                for s in self.spans
            ],
        }


# ── In-memory store ───────────────────────────────────────────────────────────
class TelemetryStore:
    """
    Rolling in-memory store for the last MAX_TRACES completed traces.
    Thread-safe for asyncio (single-threaded event loop).
    """
    MAX_TRACES = 500

    def __init__(self):
        self._traces:   deque[Trace]       = deque(maxlen=self.MAX_TRACES)
        self._active:   dict[str, Trace]   = {}   # trace_id → in-flight trace

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self, trace_id: str, tenant_id: str, question: str) -> Trace:
        t = Trace(trace_id=trace_id, tenant_id=tenant_id, question=question)
        self._active[trace_id] = t
        return t

    def finish(
        self,
        trace_id: str,
        *,
        validation_passed: bool = True,
        retry_count: int = 0,
        error: str = "",
    ) -> Trace | None:
        t = self._active.pop(trace_id, None)
        if t is None:
            return None
        t.ended_at          = time.monotonic()
        t.validation_passed = validation_passed
        t.retry_count       = retry_count
        t.error             = error
        self._traces.append(t)
        logger.info(
            "trace[%s] finished: latency=%.0fms cost=$%.5f tokens=%d retries=%d passed=%s",
            trace_id[:8], t.total_latency_ms, t.total_cost_usd,
            t.total_tokens, retry_count, validation_passed,
        )
        return t

    def add_span(self, trace_id: str, span: Span) -> None:
        t = self._active.get(trace_id)
        if t:
            t.spans.append(span)

    # ── Queries ───────────────────────────────────────────────────────────────
    def recent(self, n: int = 50, tenant_id: str | None = None) -> list[Trace]:
        traces = list(self._traces)
        if tenant_id:
            traces = [t for t in traces if t.tenant_id == tenant_id]
        return list(reversed(traces))[:n]

    def summary(self, tenant_id: str | None = None) -> dict[str, Any]:
        traces = self.recent(n=self.MAX_TRACES, tenant_id=tenant_id)
        if not traces:
            return {"total_traces": 0}

        completed = [t for t in traces if t.ended_at]
        failed_validation = [t for t in completed if not t.validation_passed]
        retried = [t for t in completed if t.retry_count > 0]
        errored = [t for t in completed if t.error]

        latencies = [t.total_latency_ms for t in completed]
        costs     = [t.total_cost_usd   for t in completed]
        tokens    = [t.total_tokens     for t in completed]

        def _avg(lst): return round(sum(lst) / len(lst), 2) if lst else 0
        def _p95(lst):
            if not lst: return 0
            s = sorted(lst)
            return round(s[int(len(s) * 0.95)], 2)

        # Per-step breakdown
        step_stats: dict[str, dict] = {}
        for t in completed:
            for s in t.spans:
                if s.step not in step_stats:
                    step_stats[s.step] = {"latencies": [], "costs": [], "tokens": []}
                step_stats[s.step]["latencies"].append(s.latency_ms)
                step_stats[s.step]["costs"].append(s.cost_usd)
                step_stats[s.step]["tokens"].append(s.input_tokens + s.output_tokens)

        step_summary = {
            step: {
                "avg_latency_ms": _avg(v["latencies"]),
                "avg_cost_usd":   round(_avg(v["costs"]), 6),
                "avg_tokens":     _avg(v["tokens"]),
                "calls":          len(v["latencies"]),
            }
            for step, v in step_stats.items()
        }

        return {
            "total_traces":          len(completed),
            "active_traces":         len(self._active),
            "avg_latency_ms":        _avg(latencies),
            "p95_latency_ms":        _p95(latencies),
            "avg_cost_usd":          round(_avg(costs), 6),
            "total_cost_usd":        round(sum(costs), 4),
            "avg_tokens":            _avg(tokens),
            "validation_failure_rate": round(len(failed_validation) / len(completed), 3) if completed else 0,
            "retry_rate":            round(len(retried) / len(completed), 3) if completed else 0,
            "error_rate":            round(len(errored) / len(completed), 3) if completed else 0,
            "step_breakdown":        step_summary,
        }


# ── Singleton store ───────────────────────────────────────────────────────────
_store = TelemetryStore()


def get_store() -> TelemetryStore:
    return _store


# ── Public helpers (called from instrumented modules) ─────────────────────────
def start_trace(trace_id: str, tenant_id: str, question: str) -> None:
    """Call at the beginning of a request. Sets the context variable."""
    _current_trace_id.set(trace_id)
    _store.start(trace_id, tenant_id, question)


def finish_trace(
    trace_id: str,
    *,
    validation_passed: bool = True,
    retry_count: int = 0,
    error: str = "",
) -> Trace | None:
    return _store.finish(
        trace_id,
        validation_passed=validation_passed,
        retry_count=retry_count,
        error=error,
    )


def record_llm_span(
    step: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    metadata: dict | None = None,
) -> None:
    """Record an LLM call span under the current trace."""
    tid = current_trace_id()
    if not tid:
        return
    span = Span(
        step=step,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        metadata=metadata or {},
    )
    _store.add_span(tid, span)


def record_latency_span(
    step: str,
    latency_ms: float,
    metadata: dict | None = None,
) -> None:
    """Record a non-LLM step (retrieval, reranking) under the current trace."""
    tid = current_trace_id()
    if not tid:
        return
    span = Span(
        step=step,
        latency_ms=latency_ms,
        metadata=metadata or {},
    )
    _store.add_span(tid, span)


# ── Context manager for timing ─────────────────────────────────────────────────
class timed:
    """
    Async context manager that measures wall-clock time and records a span.

    Usage:
        async with telemetry.timed("retrieval", metadata={"docs": 10}):
            docs = await hybrid_retrieve(...)
    """
    def __init__(self, step: str, metadata: dict | None = None):
        self.step     = step
        self.metadata = metadata or {}
        self._start   = 0.0

    async def __aenter__(self):
        self._start = time.monotonic()
        return self

    async def __aexit__(self, *_):
        elapsed_ms = (time.monotonic() - self._start) * 1000
        record_latency_span(self.step, elapsed_ms, self.metadata)
