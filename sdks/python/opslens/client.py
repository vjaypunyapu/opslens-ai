"""
OpsLens AI Python SDK — Client
===============================
Lightweight wrapper around the OpsLens AI HTTP API.

Quickstart
----------
    from opslens import OpsLens

    client = OpsLens(api_key="opsl_...")

    # Push a log error into the alert pipeline
    client.ingest(
        service="payment-service",
        error="PaymentError: Stripe timeout after 30s",
        severity="p1",
        count=47,
    )
"""

from __future__ import annotations

import datetime
import os
from typing import Any

from .exceptions import AuthenticationError, OpsLensError, RateLimitError

try:
    import httpx
    _SYNC_AVAILABLE = True
except ImportError:
    _SYNC_AVAILABLE = False

try:
    import httpx as _httpx
    _ASYNC_AVAILABLE = True
except ImportError:
    _ASYNC_AVAILABLE = False

_DEFAULT_BASE_URL = "https://opslensai.com"


class IngestResult:
    """Result from a successful ingest call."""

    def __init__(self, data: dict):
        self.status: str = data.get("status", "")
        self.message: str = data.get("message", "")
        self.task_id: str | None = data.get("task_id")
        self.error_signature: str = data.get("error_signature", "")

    def __repr__(self) -> str:
        return (
            f"IngestResult(status={self.status!r}, "
            f"signature={self.error_signature!r}, task_id={self.task_id!r})"
        )


class OpsLens:
    """
    OpsLens AI client.

    Parameters
    ----------
    api_key:
        Your OpsLens API key (starts with ``opsl_``).
        Falls back to the ``OPSLENS_API_KEY`` environment variable.
    base_url:
        Override for self-hosted or staging deployments.
        Defaults to ``https://opslensai.com``.
    timeout:
        Request timeout in seconds. Default: 10.

    Examples
    --------
    **Synchronous usage**::

        from opslens import OpsLens

        client = OpsLens(api_key="opsl_...")
        result = client.ingest(
            service="auth-service",
            error="NullPointerException in token validation",
            severity="p2",
            count=5,
        )
        print(result.status)  # "queued"

    **Async usage**::

        import asyncio
        from opslens import OpsLens

        async def main():
            client = OpsLens(api_key="opsl_...")
            result = await client.ingest_async(
                service="auth-service",
                error="NullPointerException in token validation",
                severity="p2",
                count=5,
            )

        asyncio.run(main())

    **Exception handler integration**::

        import sys
        from opslens import OpsLens

        client = OpsLens(api_key="opsl_...")

        def handle_exception(exc_type, exc_value, exc_tb):
            client.ingest(
                service="my-service",
                error=f"{exc_type.__name__}: {exc_value}",
                severity="p1",
            )
            sys.__excepthook__(exc_type, exc_value, exc_tb)

        sys.excepthook = handle_exception
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPSLENS_API_KEY") or ""
        if not self._api_key:
            raise AuthenticationError(
                "No API key provided. Pass api_key= or set OPSLENS_API_KEY."
            )
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # ── Public sync API ─────────────────────────────────────────────────────────

    def ingest(
        self,
        service: str,
        error: str,
        *,
        severity: str = "p2",
        count: int = 1,
        log_level: str = "ERROR",
        timestamp: datetime.datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """
        Push a log error event into the OpsLens AI alert pipeline.

        The event is immediately routed through your configured routing rules
        and triggers an AI-generated incident brief if thresholds are met.

        Parameters
        ----------
        service:
            Name of the service or container, e.g. ``"payment-service"``.
        error:
            The error or exception text, e.g. ``"PaymentError: timeout after 30s"``.
        severity:
            Priority level: ``"p0"`` (critical) through ``"p3"`` (low).
            Default: ``"p2"``.
        count:
            Number of occurrences in the current window. Default: ``1``.
        log_level:
            Log level string: ``"ERROR"``, ``"CRITICAL"``, ``"FATAL"``, ``"WARN"``.
        timestamp:
            ISO 8601 datetime of the first occurrence. Defaults to now.
        metadata:
            Arbitrary key/value pairs forwarded to the incident brief.

        Returns
        -------
        IngestResult
            Contains ``status``, ``message``, ``task_id``, and ``error_signature``.

        Raises
        ------
        AuthenticationError
            If the API key is invalid.
        RateLimitError
            If you exceed the plan's ingestion rate limit.
        OpsLensError
            For all other API errors.
        """
        import httpx

        payload = self._build_payload(service, error, severity, count, log_level, timestamp, metadata)
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base_url}/api/v1/log-ops/ingest",
                json=payload,
                headers=self._headers(),
            )
        return self._handle_response(resp)

    # ── Public async API ────────────────────────────────────────────────────────

    async def ingest_async(
        self,
        service: str,
        error: str,
        *,
        severity: str = "p2",
        count: int = 1,
        log_level: str = "ERROR",
        timestamp: datetime.datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """
        Async version of :meth:`ingest`. Use inside ``async`` functions.
        """
        import httpx

        payload = self._build_payload(service, error, severity, count, log_level, timestamp, metadata)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/api/v1/log-ops/ingest",
                json=payload,
                headers=self._headers(),
            )
        return self._handle_response(resp)

    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "opslens-python/0.1.0",
        }

    def _build_payload(
        self,
        service: str,
        error: str,
        severity: str,
        count: int,
        log_level: str,
        timestamp: datetime.datetime | str | None,
        metadata: dict | None,
    ) -> dict:
        ts: str | None = None
        if isinstance(timestamp, datetime.datetime):
            ts = timestamp.isoformat()
        elif isinstance(timestamp, str):
            ts = timestamp

        payload: dict[str, Any] = {
            "service_name": service,
            "error_message": error,
            "severity": severity,
            "error_count": count,
            "log_level": log_level,
        }
        if ts:
            payload["timestamp"] = ts
        if metadata:
            payload["metadata"] = metadata
        return payload

    def _handle_response(self, resp: Any) -> IngestResult:
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                "Invalid or missing API key.", status_code=resp.status_code
            )
        if resp.status_code == 429:
            raise RateLimitError(
                "Rate limit exceeded. Slow down or upgrade your plan.",
                status_code=429,
            )
        if resp.status_code not in (200, 201, 202):
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise OpsLensError(
                f"API error {resp.status_code}: {detail}",
                status_code=resp.status_code,
            )
        return IngestResult(resp.json())
