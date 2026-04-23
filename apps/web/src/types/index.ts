// ─── Shared TypeScript types ────────────────────────────────────────────────

export type Role = "admin" | "member" | "viewer";
export type InsightMagnitude = "critical" | "high" | "medium" | "low";
export type InsightStatus = "active" | "resolved" | "snoozed";
export type InsightType =
  | "complaint_spike"
  | "feature_trend"
  | "release_correlation"
  | "eng_bottleneck"
  | "churn_risk";
export type SourceType =
  | "slack"
  | "jira"
  | "google_drive"
  | "zendesk"
  | "github"
  | "bitbucket"
  | "hubspot"
  | "elasticsearch"
  | "datadog"
  | "cloudwatch"
  | "splunk"
  | "azure_monitor"
  | "gcp_logging"
  | "railway"
  | "rrt_brief";
export type AlertOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "contains";

// ─── Chat ─────────────────────────────────────────────────────────────────────
export interface ChatSession {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageSource {
  title: string;
  url: string | null;
  source_type: SourceType;
  score: number;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  sources: MessageSource[];
  token_count: number | null;
  latency_ms: number | null;
  feedback: "thumbs_up" | "thumbs_down" | null;
  created_at: string;
}

// ─── Insights ─────────────────────────────────────────────────────────────────
export interface Insight {
  id: string;
  tenant_id?: string;
  insight_type: InsightType;
  title: string;
  summary: string;
  magnitude: InsightMagnitude;
  confidence: number | null;
  status: InsightStatus;
  source_types: SourceType[];
  evidence?: Record<string, unknown>;
  snoozed_until: string | null;
  generated_at: string;   // what the API actually returns
  created_at?: string;    // alias kept for backwards compat
  updated_at?: string;
}

export interface InsightSummary {
  total: number;
  active: number;
  resolved: number;
  snoozed: number;
  by_type: Record<InsightType, number>;
  by_magnitude: Record<InsightMagnitude, number>;
}

// ─── Alerts ───────────────────────────────────────────────────────────────────
export interface AlertCondition {
  field: string;
  operator: AlertOperator;
  value: string | number;
}

export interface AlertChannel {
  type: "slack" | "email";
  webhook_url?: string;
  email?: string;
}

export interface AlertRule {
  id: string;
  name: string;
  description: string | null;
  conditions: AlertCondition[];
  channels: AlertChannel[];
  is_active: boolean;
  cooldown_minutes: number;
  last_triggered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AlertHistoryEntry {
  id: string;
  rule_id: string;
  rule_name: string;
  trigger_data: Record<string, unknown>;
  channels_notified: string[];
  triggered_at: string;
}

// ─── Integrations ─────────────────────────────────────────────────────────────
export interface Integration {
  id: string;
  source_type: SourceType;
  status: "pending" | "active" | "error" | "disconnected";
  last_synced_at: string | null;
  total_records: number;
  created_at: string;
}
