import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { InsightMagnitude, InsightType, SourceType } from "@/types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const MAGNITUDE_COLORS: Record<InsightMagnitude, string> = {
  critical: "bg-red-100 text-red-800 border-red-200",
  high:     "bg-orange-100 text-orange-800 border-orange-200",
  medium:   "bg-yellow-100 text-yellow-800 border-yellow-200",
  low:      "bg-green-100 text-green-800 border-green-200",
};

export const MAGNITUDE_DOT: Record<InsightMagnitude, string> = {
  critical: "bg-red-500",
  high:     "bg-orange-500",
  medium:   "bg-yellow-500",
  low:      "bg-green-500",
};

export const INSIGHT_TYPE_LABELS: Record<InsightType, string> = {
  complaint_spike:       "Complaint Spike",
  feature_trend:         "Feature Trend",
  release_correlation:   "Release Correlation",
  eng_bottleneck:        "Eng Bottleneck",
  churn_risk:            "Churn Risk",
};

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
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
  rrt_brief:     "Incident Brief",
};

export const SOURCE_TYPE_ICONS: Record<SourceType, string> = {
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
  rrt_brief:     "🚨",
};
