"use client";
import { useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { ChevronDown, ChevronUp, CheckCircle2, BellOff, RotateCcw } from "lucide-react";
import { Insight, InsightMagnitude, InsightType, SourceType } from "@/types";

interface Props {
  insight: Insight;
  onStatusChange: (id: string, status: "resolved" | "snoozed" | "active", snoozeHours?: number) => void;
}

// ── Inline colour maps (no Tailwind) ────────────────────────────────────────

const MAG_DOT: Record<string, string> = {
  high:   "#f97316",   // orange-500
  medium: "#eab308",   // yellow-500
  low:    "#22c55e",   // green-500
};

const MAG_BADGE_BG: Record<string, string> = {
  high:   "rgba(249,115,22,0.15)",
  medium: "rgba(234,179,8,0.15)",
  low:    "rgba(34,197,94,0.15)",
};

const MAG_BADGE_COLOR: Record<string, string> = {
  high:   "#fb923c",
  medium: "#fbbf24",
  low:    "#4ade80",
};

const TYPE_LABELS: Record<string, string> = {
  complaint_spike:     "Complaint Spike",
  feature_trend:       "Feature Trend",
  release_correlation: "Release Correlation",
  eng_bottleneck:      "Eng Bottleneck",
  churn_risk:          "Churn Risk",
};

const SOURCE_ICONS: Record<string, string> = {
  slack:         "💬",
  jira:          "🎯",
  google_drive:  "📄",
  zendesk:       "🎫",
  github:        "🐙",
  bitbucket:     "🪣",
  hubspot:       "🔶",
  elasticsearch: "🔍",
  datadog:       "🐶",
  cloudwatch:    "☁️",
  splunk:        "🔦",
  azure_monitor: "🔷",
  gcp_logging:   "🌐",
  railway:       "🚂",
};

const SOURCE_LABELS: Record<string, string> = {
  slack:         "Slack",
  jira:          "Jira",
  google_drive:  "Google Drive",
  zendesk:       "Zendesk",
  github:        "GitHub",
  bitbucket:     "Bitbucket",
  hubspot:       "HubSpot",
  elasticsearch: "Elasticsearch",
  datadog:       "Datadog",
  cloudwatch:    "AWS CloudWatch",
  splunk:        "Splunk",
  azure_monitor: "Azure Monitor",
  gcp_logging:   "GCP Logging",
  railway:       "Railway",
};

export function InsightCard({ insight, onStatusChange }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [hoverResolve,  setHoverResolve]  = useState(false);
  const [hoverSnooze,   setHoverSnooze]   = useState(false);
  const [hoverReopen,   setHoverReopen]   = useState(false);
  const [hoverExpand,   setHoverExpand]   = useState(false);

  const isResolved = insight.status === "resolved";
  const isSnoozed  = insight.status === "snoozed";

  const mag      = insight.magnitude ?? "low";
  const dotColor = MAG_DOT[mag]       ?? "#64748b";
  const badgeBg  = MAG_BADGE_BG[mag]  ?? "rgba(100,116,139,0.15)";
  const badgeClr = MAG_BADGE_COLOR[mag] ?? "#94a3b8";

  const card: React.CSSProperties = {
    background:   isResolved ? "rgba(15,23,42,0.5)" : "#131f35",
    border:       `1px solid ${isResolved ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.09)"}`,
    borderRadius: 12,
    overflow:     "hidden",
    opacity:      isResolved ? 0.65 : 1,
    transition:   "box-shadow 0.15s",
  };

  const header: React.CSSProperties = {
    padding: "14px 16px",
    display: "flex",
    alignItems: "flex-start",
    gap: 12,
  };

  const dot: React.CSSProperties = {
    marginTop: 5,
    width: 9,
    height: 9,
    borderRadius: "50%",
    background: dotColor,
    flexShrink: 0,
  };

  const meta: React.CSSProperties = {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "center",
    gap: 8,
    marginBottom: 6,
  };

  const typeLabel: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 500,
    color: "#64748b",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  };

  const badge: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: 999,
    background: badgeBg,
    color: badgeClr,
    border: `1px solid ${badgeClr}33`,
  };

  const confidenceLabel: React.CSSProperties = {
    fontSize: 11,
    color: "#475569",
  };

  const title: React.CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    color: "#e2e8f0",
    lineHeight: 1.45,
  };

  const pills: React.CSSProperties = {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: 6,
    marginTop: 8,
  };

  const pill: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    fontSize: 11,
    background: "rgba(255,255,255,0.05)",
    border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: 999,
    padding: "2px 8px",
    color: "#94a3b8",
  };

  const tsAndToggle: React.CSSProperties = {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "flex-end",
    gap: 6,
    flexShrink: 0,
  };

  const ts: React.CSSProperties = {
    fontSize: 11,
    color: "#475569",
    whiteSpace: "nowrap" as const,
  };

  const expandBtn: React.CSSProperties = {
    background: "transparent",
    border: "none",
    color: hoverExpand ? "#94a3b8" : "#475569",
    cursor: "pointer",
    padding: 2,
    display: "flex",
    alignItems: "center",
    transition: "color 0.15s",
  };

  const expandedBody: React.CSSProperties = {
    borderTop: "1px solid rgba(255,255,255,0.07)",
    padding: "12px 16px",
    background: "rgba(0,0,0,0.2)",
  };

  const summaryText: React.CSSProperties = {
    fontSize: 13,
    color: "#94a3b8",
    lineHeight: 1.6,
    margin: 0,
  };

  const actions: React.CSSProperties = {
    borderTop: "1px solid rgba(255,255,255,0.07)",
    padding: "8px 16px",
    display: "flex",
    alignItems: "center",
    gap: 14,
  };

  const actionBtn = (hovered: boolean, hoverColor: string): React.CSSProperties => ({
    background: "transparent",
    border: "none",
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    fontSize: 12,
    fontWeight: 500,
    color: hovered ? hoverColor : "#475569",
    cursor: "pointer",
    padding: 0,
    transition: "color 0.15s",
  });

  const statusChip: React.CSSProperties = {
    marginLeft: "auto",
    fontSize: 11,
    color: "#334155",
    textTransform: "capitalize" as const,
  };

  return (
    <div style={card}>
      {/* Header */}
      <div style={header}>
        <span style={dot} />

        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Type + magnitude badge */}
          <div style={meta}>
            <span style={typeLabel}>
              {TYPE_LABELS[insight.insight_type] ?? insight.insight_type}
            </span>
            <span style={badge}>
              {mag.charAt(0).toUpperCase() + mag.slice(1)}
            </span>
            {insight.confidence !== null && insight.confidence !== undefined && (
              <span style={confidenceLabel}>
                {Math.round((insight.confidence ?? 0) * 100)}% confidence
              </span>
            )}
          </div>

          {/* Title */}
          <p style={title}>{insight.title}</p>

          {/* Source pills */}
          <div style={pills}>
            {insight.source_types.map((st) => (
              <span key={st} style={pill}>
                {SOURCE_ICONS[st] ?? "🔌"} {SOURCE_LABELS[st] ?? st}
              </span>
            ))}
          </div>
        </div>

        {/* Timestamp + expand toggle */}
        <div style={tsAndToggle}>
          <span style={ts}>
            {(() => {
              try {
                const rawTs = insight.generated_at ?? insight.created_at;
                return rawTs ? formatDistanceToNow(new Date(rawTs), { addSuffix: true }) : "—";
              } catch {
                return "—";
              }
            })()}
          </span>
          <button
            style={expandBtn}
            onClick={() => setExpanded((v) => !v)}
            onMouseEnter={() => setHoverExpand(true)}
            onMouseLeave={() => setHoverExpand(false)}
          >
            {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
        </div>
      </div>

      {/* Expanded summary */}
      {expanded && (
        <div style={expandedBody}>
          <p style={summaryText}>{insight.summary}</p>
        </div>
      )}

      {/* Actions */}
      <div style={actions}>
        {!isResolved && (
          <button
            style={actionBtn(hoverResolve, "#4ade80")}
            onClick={() => onStatusChange(insight.id, "resolved")}
            onMouseEnter={() => setHoverResolve(true)}
            onMouseLeave={() => setHoverResolve(false)}
          >
            <CheckCircle2 size={13} />
            Resolve
          </button>
        )}
        {!isSnoozed && !isResolved && (
          <button
            style={actionBtn(hoverSnooze, "#fbbf24")}
            onClick={() => onStatusChange(insight.id, "snoozed", 24)}
            onMouseEnter={() => setHoverSnooze(true)}
            onMouseLeave={() => setHoverSnooze(false)}
          >
            <BellOff size={13} />
            Snooze 24h
          </button>
        )}
        {(isResolved || isSnoozed) && (
          <button
            style={actionBtn(hoverReopen, "#2dd4bf")}
            onClick={() => onStatusChange(insight.id, "active")}
            onMouseEnter={() => setHoverReopen(true)}
            onMouseLeave={() => setHoverReopen(false)}
          >
            <RotateCcw size={13} />
            Reopen
          </button>
        )}
        <span style={statusChip}>{insight.status}</span>
      </div>
    </div>
  );
}
