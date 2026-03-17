"""
OpsLens AI — Insight Engine Tests
====================================
Unit tests for all five insight detectors.
Uses pytest-asyncio + factory_boy fixtures.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.worker.tasks.insight_engine import (
    ChurnRiskDetector,
    ComplaintSpikeDetector,
    DetectedInsight,
    EngBottleneckDetector,
    FeatureTrendDetector,
    ReleaseCorrelationDetector,
    save_insight,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────
TENANT_ID = "11111111-1111-1111-1111-111111111111"
NOW = datetime.now(tz=timezone.utc)


def make_doc(**kwargs):
    """Create a minimal CanonicalDocument mock."""
    doc = MagicMock()
    doc.id              = kwargs.get("id", "doc-001")
    doc.tenant_id       = kwargs.get("tenant_id", TENANT_ID)
    doc.source_type     = kwargs.get("source_type", "zendesk")
    doc.source_id       = kwargs.get("source_id", "T-001")
    doc.title           = kwargs.get("title", "Test document")
    doc.content         = kwargs.get("content", "Sample content about errors and issues")
    doc.author          = kwargs.get("author", "user@example.com")
    doc.url             = kwargs.get("url", "https://app.example.com/tickets/001")
    doc.doc_metadata    = kwargs.get("doc_metadata", {})
    doc.source_created_at = kwargs.get("source_created_at", NOW - timedelta(hours=2))
    doc.source_updated_at = kwargs.get("source_updated_at", NOW - timedelta(hours=1))
    return doc


def make_llm_response(insight_type: str, title: str, summary: str, magnitude: float = 35.0) -> str:
    return json.dumps({
        "title":        title,
        "summary":      summary,
        "magnitude":    magnitude,
        "insight_type": insight_type,
        "confidence":   "high",
    })


# ── ComplaintSpikeDetector tests ──────────────────────────────────────────────
class TestComplaintSpikeDetector:

    def test_build_summary_includes_counts(self):
        detector = ComplaintSpikeDetector()
        docs = [
            make_doc(source_type="zendesk", title="Login error", content="error broken fail",
                     source_created_at=NOW - timedelta(days=i))
            for i in range(10)
        ]
        summary = detector.build_summary(docs)
        assert "COMPLAINT SPIKE ANALYSIS" in summary
        assert "Total records (7 days): 10" in summary
        assert "zendesk" in summary

    @pytest.mark.asyncio
    async def test_run_returns_insight_when_pattern_found(self):
        detector = ComplaintSpikeDetector()
        docs = [
            make_doc(source_type="zendesk", content="checkout error broken payment fail")
            for _ in range(20)
        ]

        llm_content = make_llm_response(
            "complaint_spike",
            "Checkout errors up 35% this week",
            "Payment gateway errors spiked significantly, concentrated in checkout flow.",
        )

        mock_llm_result = MagicMock()
        mock_llm_result.content = llm_content

        with (
            patch.object(detector, "collect_data", new=AsyncMock(return_value=docs)),
            patch("apps.worker.tasks.insight_engine._ANALYSIS_PROMPT", MagicMock()),
            patch("apps.worker.tasks.insight_engine._llm") as mock_llm,
        ):
            mock_chain = AsyncMock()
            mock_chain.ainvoke = AsyncMock(return_value=mock_llm_result)
            mock_llm.__or__ = MagicMock(return_value=mock_chain)

            with patch.object(detector, "_call_llm", new=AsyncMock(return_value={
                "title":        "Checkout errors up 35% this week",
                "summary":      "Payment gateway errors spiked significantly.",
                "magnitude":    35.0,
                "insight_type": "complaint_spike",
                "confidence":   "high",
            })):
                result = await detector.run(TENANT_ID)

        assert result is not None
        assert isinstance(result, DetectedInsight)
        assert result.insight_type == "complaint_spike"
        assert result.magnitude == 35.0
        assert result.confidence == "high"

    @pytest.mark.asyncio
    async def test_run_returns_none_when_insufficient_data(self):
        detector = ComplaintSpikeDetector()
        with patch.object(detector, "collect_data", new=AsyncMock(return_value=[])):
            result = await detector.run(TENANT_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_run_returns_none_when_llm_returns_no_signal(self):
        detector = ComplaintSpikeDetector()
        docs = [make_doc() for _ in range(10)]
        with (
            patch.object(detector, "collect_data", new=AsyncMock(return_value=docs)),
            patch.object(detector, "_call_llm", new=AsyncMock(return_value={"insight_type": "none"})),
        ):
            result = await detector.run(TENANT_ID)
        assert result is None


# ── FeatureTrendDetector tests ─────────────────────────────────────────────────
class TestFeatureTrendDetector:

    def test_build_summary_includes_source_breakdown(self):
        detector = FeatureTrendDetector()
        docs = [
            make_doc(source_type="jira", title="Feature request: dark mode"),
            make_doc(source_type="zendesk", title="Customer asks for CSV export"),
            make_doc(source_type="slack", title="feature: mobile app"),
        ]
        summary = detector.build_summary(docs)
        assert "FEATURE REQUEST TREND ANALYSIS" in summary
        assert "jira" in summary
        assert "zendesk" in summary
        assert "3" in summary  # total count

    @pytest.mark.asyncio
    async def test_run_returns_insight_with_count(self):
        detector = FeatureTrendDetector()
        docs = [
            make_doc(source_type="jira", title=f"Feature request: dark mode #{i}")
            for i in range(15)
        ]
        with (
            patch.object(detector, "collect_data", new=AsyncMock(return_value=docs)),
            patch.object(detector, "_call_llm", new=AsyncMock(return_value={
                "title":        "Dark mode requested by 15 customers",
                "summary":      "Dark mode is the top feature request with 15 occurrences.",
                "magnitude":    15.0,
                "insight_type": "feature_trend",
                "confidence":   "high",
            })),
        ):
            result = await detector.run(TENANT_ID)

        assert result is not None
        assert result.insight_type == "feature_trend"
        assert result.magnitude == 15.0


# ── ReleaseCorrelationDetector tests ──────────────────────────────────────────
class TestReleaseCorrelationDetector:

    def test_build_summary_identifies_time_proximity(self):
        detector = ReleaseCorrelationDetector()
        release_time = NOW - timedelta(days=2)
        error_time   = NOW - timedelta(days=2, hours=3)  # 3h after release

        release = make_doc(
            source_type="github",
            title="Release v3.2.0",
            source_created_at=release_time,
        )
        error = make_doc(
            source_type="jira",
            title="Login error spike after deploy",
            source_created_at=error_time,
        )
        summary = detector.build_summary([release, error])
        assert "RELEASE CORRELATION ANALYSIS" in summary
        assert "v3.2.0" in summary

    @pytest.mark.asyncio
    async def test_run_returns_correlation_insight(self):
        detector = ReleaseCorrelationDetector()
        release_time = NOW - timedelta(days=1)
        docs = [
            make_doc(source_type="github", title="Release v4.0", source_created_at=release_time),
        ] + [
            make_doc(source_type="jira", title="Login broken",
                     source_created_at=release_time + timedelta(hours=i))
            for i in range(1, 6)
        ]
        with (
            patch.object(detector, "collect_data", new=AsyncMock(return_value=docs)),
            patch.object(detector, "_call_llm", new=AsyncMock(return_value={
                "title":        "Release v4.0 correlated with login error spike",
                "summary":      "5 login errors reported within 24h of v4.0 release.",
                "magnitude":    5.0,
                "insight_type": "release_correlation",
                "confidence":   "medium",
            })),
        ):
            result = await detector.run(TENANT_ID)

        assert result is not None
        assert result.insight_type == "release_correlation"


# ── EngBottleneckDetector tests ────────────────────────────────────────────────
class TestEngBottleneckDetector:

    def test_build_summary_calculates_stale_days(self):
        detector = EngBottleneckDetector()
        docs = [
            make_doc(
                source_type="jira",
                title="PROD-441: Fix payment timeout",
                doc_metadata={"status": "In Progress", "assignee": "alice", "priority": "Critical"},
                source_updated_at=NOW - timedelta(days=7),
            ),
            make_doc(
                source_type="jira",
                title="PROD-502: Update auth service",
                doc_metadata={"status": "Blocked", "assignee": "bob", "priority": "High"},
                source_updated_at=NOW - timedelta(days=10),
            ),
        ]
        summary = detector.build_summary(docs)
        assert "ENGINEERING BOTTLENECK" in summary
        assert "Stalled tickets: 2" in summary
        assert "alice" in summary
        assert "bob" in summary

    @pytest.mark.asyncio
    async def test_min_records_is_2(self):
        detector = EngBottleneckDetector()
        assert detector._min_records() == 2

    @pytest.mark.asyncio
    async def test_run_returns_none_for_single_ticket(self):
        detector = EngBottleneckDetector()
        with patch.object(detector, "collect_data", new=AsyncMock(return_value=[make_doc()])):
            result = await detector.run(TENANT_ID)
        assert result is None


# ── ChurnRiskDetector tests ───────────────────────────────────────────────────
class TestChurnRiskDetector:

    def test_build_summary_flags_at_risk_companies(self):
        detector = ChurnRiskDetector()
        docs = [
            make_doc(
                source_type="zendesk",
                doc_metadata={"priority": "urgent", "status": "open", "organization_name": "Acme Corp"},
            )
            for _ in range(4)    # Acme Corp has 4 critical tickets → at-risk
        ] + [
            make_doc(
                source_type="hubspot",
                doc_metadata={"dealstage": "renewal", "closedate": "2026-04-01"},
                title="Acme Corp Renewal Q2 2026",
            )
        ]
        summary = detector.build_summary(docs)
        assert "CHURN RISK ANALYSIS" in summary
        assert "Acme Corp" in summary

    @pytest.mark.asyncio
    async def test_run_returns_churn_insight(self):
        detector = ChurnRiskDetector()
        docs = [
            make_doc(source_type="zendesk", doc_metadata={"priority": "urgent", "organization_name": "BigCo"})
            for _ in range(5)
        ]
        with (
            patch.object(detector, "collect_data", new=AsyncMock(return_value=docs)),
            patch.object(detector, "_call_llm", new=AsyncMock(return_value={
                "title":        "BigCo at high churn risk — 5 critical tickets open",
                "summary":      "BigCo has 5 unresolved critical support tickets with renewal approaching.",
                "magnitude":    1.0,
                "insight_type": "churn_risk",
                "confidence":   "high",
            })),
        ):
            result = await detector.run(TENANT_ID)

        assert result is not None
        assert result.insight_type == "churn_risk"


# ── save_insight dedup test ───────────────────────────────────────────────────
class TestSaveInsight:

    @pytest.mark.asyncio
    async def test_duplicate_insight_is_skipped(self):
        """save_insight should return None if identical title exists in last 24h."""
        detected = DetectedInsight(
            title="Checkout errors up 35%",
            summary="Test summary",
            magnitude=35.0,
            insight_type="complaint_spike",
            source_types=["zendesk"],
            raw_data={},
        )

        existing_mock = MagicMock()
        existing_mock.scalar_one_or_none = MagicMock(return_value=MagicMock())  # found existing

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=existing_mock)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("apps.worker.tasks.insight_engine.AsyncSession", return_value=mock_session):
            result = await save_insight(TENANT_ID, detected)

        assert result is None
