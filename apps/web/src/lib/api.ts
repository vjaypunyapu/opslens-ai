/**
 * API client — thin wrapper around fetch that:
 *  - Prefixes all requests with /api/v1
 *  - Attaches the Clerk session token automatically
 *  - Returns typed responses
 *  - Throws ApiError on non-2xx responses
 */
import {
  AlertHistoryEntry,
  AlertRule,
  ChatMessage,
  ChatSession,
  Insight,
  InsightSummary,
  Integration,
} from "@/types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, ...init } = options;
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
export const dashboardApi = {
  get: (token: string) => request<DashboardData>("/dashboard", { token }),
};

export interface DashboardData {
  insights: {
    active: number; resolved: number; snoozed: number; new_7d: number;
    recent: { id: string; title: string; insight_type: string; magnitude: string; status: string; generated_at: string }[];
  };
  documents: { total: number; indexed_7d: number; by_source: Record<string, number> };
  integrations: {
    total: number; active: number;
    sources: { id: string; source_type: string; status: string; last_synced_at: string | null }[];
  };
  chat: {
    total_sessions: number; sessions_7d: number;
    recent_sessions: { id: string; title: string; updated_at: string }[];
  };
  alerts: { active_rules: number };
  generated_at: string;
}

// ─── Chat ─────────────────────────────────────────────────────────────────────
export const chatApi = {
  listSessions: (token: string) =>
    request<ChatSession[]>("/chat/sessions", { token }),

  createSession: (token: string) =>
    request<ChatSession>("/chat/sessions", { method: "POST", token }),

  getMessages: (sessionId: string, token: string) =>
    request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`, { token }),

  /** Returns an EventSource-compatible ReadableStream for SSE. */
  streamQuery: (
    sessionId: string,
    question: string,
    sourceTypes: string[] | undefined,
    token: string,
  ): EventSource => {
    // We use a custom SSE request via fetch + ReadableStream,
    // but return a simple wrapper to keep component code clean.
    // Components should use the useChat hook instead.
    throw new Error("Use useChat hook — do not call streamQuery directly.");
  },

  sendFeedback: (
    _sessionId: string,
    messageId: string,
    feedback: "thumbs_up" | "thumbs_down",
    token: string,
  ) =>
    request<void>("/chat/feedback", {
      method: "POST",
      body: JSON.stringify({
        message_id: messageId,
        score: feedback === "thumbs_up" ? 1 : -1,
      }),
      token,
    }),
};

// ─── Insights ─────────────────────────────────────────────────────────────────
export const insightsApi = {
  list: (
    token: string,
    params: { status?: string; type?: string; days?: number } = {},
  ) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ),
    ).toString();
    return request<Insight[]>(`/insights${qs ? `?${qs}` : ""}`, { token });
  },

  summary: (token: string) => request<InsightSummary>("/insights/summary", { token }),

  updateStatus: (
    id: string,
    status: "active" | "resolved" | "snoozed",
    snoozeHours?: number,
    token?: string,
  ) =>
    request<Insight>(`/insights/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status, snooze_hours: snoozeHours }),
      token,
    }),

  generate: (token: string) =>
    request<{ task_id: string }>("/insights/generate", { method: "POST", token }),
};

// ─── Alerts ───────────────────────────────────────────────────────────────────
export const alertsApi = {
  listRules: (token: string) => request<AlertRule[]>("/alerts/rules", { token }),

  createRule: (token: string, data: Omit<AlertRule, "id" | "created_at" | "updated_at" | "last_triggered_at">) =>
    request<AlertRule>("/alerts/rules", {
      method: "POST",
      body: JSON.stringify(data),
      token,
    }),

  updateRule: (id: string, data: Partial<AlertRule>, token: string) =>
    request<AlertRule>(`/alerts/rules/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
      token,
    }),

  deleteRule: (id: string, token: string) =>
    request<void>(`/alerts/rules/${id}`, { method: "DELETE", token }),

  testRule: (id: string, token: string) =>
    request<{ fired: boolean; message: string }>(`/alerts/test/${id}`, {
      method: "POST",
      token,
    }),

  listHistory: (token: string) =>
    request<AlertHistoryEntry[]>("/alerts/history", { token }),
};

// ─── Incidents ────────────────────────────────────────────────────────────────
export interface Incident {
  id: string;
  title: string;
  description: string | null;
  status: "open" | "investigating" | "analysing" | "resolved" | "closed";
  severity: "p0" | "p1" | "p2" | "p3" | "p4";
  service: string | null;
  started_at: string | null;
  resolved_at: string | null;
  root_cause: string | null;
  contributing_factors: string[];
  recommendations: string[];
  timeline: TimelineEvent[];
  signals: Signal[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineEvent {
  timestamp: string;
  source: string;
  source_type: string;
  event_type: string;
  title: string;
  detail: string;
  url: string;
  author: string;
}

export interface Signal {
  source_type: string;
  title: string;
  detail: string;
  url: string;
  timestamp: string;
  author: string;
  score: number;
}

export const incidentsApi = {
  list: (token: string, params: { status?: string; severity?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]))
    ).toString();
    return request<Incident[]>(`/incidents${qs ? `?${qs}` : ""}`, { token });
  },

  get: (id: string, token: string) => request<Incident>(`/incidents/${id}`, { token }),

  create: (token: string, data: { title: string; description?: string; service?: string; severity?: string; started_at?: string }) =>
    request<Incident>("/incidents", { method: "POST", body: JSON.stringify(data), token }),

  update: (id: string, token: string, data: Partial<Pick<Incident, "title" | "description" | "status" | "severity" | "service" | "resolved_at">>) =>
    request<Incident>(`/incidents/${id}`, { method: "PATCH", body: JSON.stringify(data), token }),

  delete: (id: string, token: string) =>
    request<void>(`/incidents/${id}`, { method: "DELETE", token }),

  investigate: (id: string, token: string) =>
    request<{ status: string; message: string }>(`/incidents/${id}/investigate`, { method: "POST", token }),
};

// ─── Admin — Teams & RBAC ─────────────────────────────────────────────────────
export interface Team {
  id: string;
  name: string;
  description: string | null;
  member_count: number;
  created_at: string;
}

export interface TeamMember {
  user_external_id: string;
  user_email: string | null;
  role: string;
  added_at: string;
}

export interface PermissionEntry {
  source_type: string;
  source_id: string;
  can_read: boolean;
  can_see_metrics: boolean;
  can_see_logs: boolean;
}

export interface OrgUser {
  external_id: string;
  email: string;
  name: string | null;
  role: string;
  created_at: string;
}

export const adminApi = {
  // Teams
  listTeams: (token: string) =>
    request<Team[]>("/admin/teams", { token }),

  createTeam: (token: string, data: { name: string; description?: string }) =>
    request<Team>("/admin/teams", { method: "POST", body: JSON.stringify(data), token }),

  deleteTeam: (token: string, teamId: string) =>
    request<void>(`/admin/teams/${teamId}`, { method: "DELETE", token }),

  // Members
  listMembers: (token: string, teamId: string) =>
    request<TeamMember[]>(`/admin/teams/${teamId}/members`, { token }),

  addMember: (token: string, teamId: string, data: { user_external_id: string; user_email?: string; role?: string }) =>
    request<TeamMember>(`/admin/teams/${teamId}/members`, { method: "POST", body: JSON.stringify(data), token }),

  removeMember: (token: string, teamId: string, userExternalId: string) =>
    request<void>(`/admin/teams/${teamId}/members/${userExternalId}`, { method: "DELETE", token }),

  // Permissions
  getPermissions: (token: string, teamId: string) =>
    request<PermissionEntry[]>(`/admin/teams/${teamId}/permissions`, { token }),

  setPermissions: (token: string, teamId: string, permissions: PermissionEntry[]) =>
    request<PermissionEntry[]>(`/admin/teams/${teamId}/permissions`, {
      method: "PUT", body: JSON.stringify({ permissions }), token,
    }),

  // Users
  listUsers: (token: string) =>
    request<OrgUser[]>("/admin/users", { token }),

  updateUserRole: (token: string, externalId: string, role: string) =>
    request<OrgUser>(`/admin/users/${externalId}/role`, {
      method: "PATCH", body: JSON.stringify({ role }), token,
    }),

  // Invites
  createInvite: (token: string, data: { email: string; team_id?: string; role?: string; team_role?: string }) =>
    request<{ invite_id: string; email: string; token: string; expires_at: string }>(
      "/admin/invites", { method: "POST", body: JSON.stringify(data), token }
    ),
};

// ─── Integrations ─────────────────────────────────────────────────────────────
export const integrationsApi = {
  list: (token: string) => request<Integration[]>("/integrations", { token }),

  connect: (
    token: string,
    sourceType: string,
    credentials: Record<string, string>,
  ) =>
    request<Integration>("/integrations", {
      method: "POST",
      body: JSON.stringify({ source_type: sourceType, credentials }),
      token,
    }),

  disconnect: (id: string, token: string) =>
    request<void>(`/integrations/${id}`, { method: "DELETE", token }),

  triggerSync: (id: string, token: string) =>
    request<{ sync_id: string }>(`/integrations/${id}/sync`, {
      method: "POST",
      token,
    }),
};

// ─── Log Ops / Simulate ───────────────────────────────────────────────────────

export interface SimulateAlertResponse {
  status: string;
  message: string;
  enrich_task_id: string | null;
  rrt_task_id: string | null;
  error_signature: string;
  service_name: string;
}

export const logOpsApi = {
  simulate: (
    token: string,
    data: {
      service_name: string;
      error_message: string;
      error_count?: number;
      severity?: string;
      webhook_url?: string;
    },
  ) =>
    request<SimulateAlertResponse>("/log-ops/simulate", {
      method: "POST",
      body: JSON.stringify(data),
      token,
    }),
};

// ─── RRT Briefs ───────────────────────────────────────────────────────────────

export interface RRTBrief {
  id: string;
  title: string;
  what_happened: string;
  impact: string | null;
  started_at: string | null;
  detected_at: string;
  suspected_cause: string | null;
  next_actions: string[];
  related_items: {
    source_type: string;
    title: string;
    url: string | null;
    snippet: string;
    score: number;
  }[];
  owner_team: string | null;
  owner_contacts: string[];
  status: string;
  resolved_at: string | null;
  resolution_notes: string | null;
  error_signature: string | null;
  error_sample: string | null;
  channels_sent: string[];
  created_at: string;
  updated_at: string;
}

export const rrtBriefsApi = {
  list: (token: string, params?: { status?: string; days?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.days)   qs.set("days",   String(params.days));
    if (params?.limit)  qs.set("limit",  String(params.limit));
    const query = qs.toString() ? `?${qs}` : "";
    return request<RRTBrief[]>(`/rrt-briefs${query}`, { token });
  },

  get: (token: string, id: string) =>
    request<RRTBrief>(`/rrt-briefs/${id}`, { token }),

  update: (token: string, id: string, data: { status?: string; resolution_notes?: string; impact?: string; owner_team?: string }) =>
    request<RRTBrief>(`/rrt-briefs/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
      token,
    }),

  postUpdate: (token: string, id: string, data: { update_text: string; status?: string }) =>
    request<{ message: string; new_status: string; slack_notified: boolean }>(
      `/rrt-briefs/${id}/update`,
      { method: "POST", body: JSON.stringify(data), token },
    ),

  generate: (
    token: string,
    data: { service_name: string; error_message: string; error_count?: number; webhook_url?: string },
  ) =>
    request<{ status: string; task_id: string; message: string; error_signature: string }>(
      "/rrt-briefs/generate",
      { method: "POST", body: JSON.stringify(data), token },
    ),

  delete: (token: string, id: string) =>
    request<void>(`/rrt-briefs/${id}`, { method: "DELETE", token }),
};
