"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { RefreshCw, Plug, CheckCircle2, XCircle, Clock, AlertCircle, X } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { cn } from "@/lib/utils";
import { SOURCE_TYPE_ICONS, SOURCE_TYPE_LABELS } from "@/lib/utils";
import { Integration, SourceType } from "@/types";
import { integrationsApi } from "@/lib/api";

type StatusVariant = "active" | "pending" | "error" | "disconnected";

// ── Credential field definitions per source ───────────────────────────────────
type CredentialField = { key: string; label: string; placeholder: string; type?: string; hint?: string };

const CREDENTIAL_FIELDS: Record<string, CredentialField[]> = {
  jira: [
    { key: "server_url",  label: "Jira Server URL",  placeholder: "https://yourcompany.atlassian.net", hint: "Your Atlassian Cloud URL" },
    { key: "email",       label: "Account Email",    placeholder: "you@company.com" },
    { key: "api_token",   label: "API Token",        placeholder: "Paste your Atlassian API token", type: "password",
      hint: "Generate at id.atlassian.com → Security → API tokens" },
  ],
  slack: [
    { key: "bot_token",   label: "Bot Token",        placeholder: "xoxb-...", type: "password",
      hint: "From your Slack App → OAuth & Permissions → Bot User OAuth Token" },
  ],
  github: [
    { key: "access_token", label: "Personal Access Token", placeholder: "ghp_...", type: "password",
      hint: "Generate at GitHub → Settings → Developer settings → Personal access tokens" },
    { key: "org",          label: "Organization or Username (optional)", placeholder: "e.g. my-org or johndoe", hint: "Your GitHub username or org name — NOT your email. Leave blank to sync your own repos." },
  ],
  bitbucket: [
    { key: "workspace",    label: "Workspace slug",  placeholder: "my-team",
      hint: "The workspace slug from your Bitbucket URL: bitbucket.org/{workspace}" },
    { key: "username",     label: "Username",         placeholder: "your-bitbucket-username",
      hint: "Your Bitbucket account username (not email)" },
    { key: "app_password", label: "App Password",     placeholder: "••••••••", type: "password",
      hint: "Generate at Bitbucket → Personal settings → App passwords (needs Repositories: Read)" },
  ],
  google_drive: [
    { key: "service_account_json", label: "Service Account JSON", placeholder: '{"type":"service_account",...}', type: "password",
      hint: "Paste the full JSON key file from Google Cloud Console → IAM → Service Accounts" },
  ],
  zendesk: [
    { key: "subdomain",  label: "Subdomain",    placeholder: "yourcompany (from yourcompany.zendesk.com)" },
    { key: "email",      label: "Agent Email",  placeholder: "agent@company.com" },
    { key: "api_token",  label: "API Token",    placeholder: "Paste your Zendesk API token", type: "password",
      hint: "Generate at Zendesk Admin → Apps & Integrations → Zendesk API" },
  ],
  hubspot: [
    { key: "access_token", label: "Private App Token", placeholder: "pat-na1-...", type: "password",
      hint: "Generate at HubSpot → Settings → Integrations → Private Apps" },
  ],
  elasticsearch: [
    { key: "url",      label: "Elasticsearch URL", placeholder: "https://your-cluster:9200" },
    { key: "username", label: "Username",           placeholder: "elastic" },
    { key: "password", label: "Password",           placeholder: "••••••••", type: "password" },
  ],
  datadog: [
    { key: "api_key", label: "API Key",         placeholder: "Paste your Datadog API key",         type: "password" },
    { key: "app_key", label: "Application Key", placeholder: "Paste your Datadog application key", type: "password" },
    { key: "site",    label: "Site",             placeholder: "datadoghq.com", hint: "e.g. datadoghq.com or datadoghq.eu" },
  ],
  cloudwatch: [
    { key: "aws_access_key_id",     label: "AWS Access Key ID",     placeholder: "AKIA..." },
    { key: "aws_secret_access_key", label: "AWS Secret Access Key", placeholder: "wJalr...", type: "password" },
    { key: "region",                label: "AWS Region",            placeholder: "us-east-1" },
  ],
  splunk: [
    { key: "host",        label: "Splunk Host",        placeholder: "https://your-splunk:8089" },
    { key: "username",    label: "Username",            placeholder: "admin" },
    { key: "password",    label: "Password",            placeholder: "••••••••", type: "password" },
  ],
  azure_monitor: [
    { key: "tenant_id",       label: "Tenant ID",       placeholder: "Azure AD Tenant ID" },
    { key: "client_id",       label: "Client ID",       placeholder: "App Registration Client ID" },
    { key: "client_secret",   label: "Client Secret",   placeholder: "••••••••", type: "password" },
    { key: "subscription_id", label: "Subscription ID", placeholder: "Azure Subscription ID" },
  ],
  gcp_logging: [
    { key: "service_account_json", label: "Service Account JSON", placeholder: '{"type":"service_account",...}', type: "password",
      hint: "Paste the GCP service account JSON key" },
    { key: "project_id", label: "GCP Project ID", placeholder: "my-gcp-project" },
  ],
  railway: [
    { key: "api_token",  label: "API Token",   placeholder: "Paste your Railway API token", type: "password",
      hint: "Generate at railway.app → Account Settings → Tokens" },
    { key: "project_id", label: "Project ID (optional)", placeholder: "Leave blank to sync all projects",
      hint: "Copy from your Railway project URL: railway.app/project/<project-id>" },
  ],
};

// ── Credentials Modal ─────────────────────────────────────────────────────────
function CredentialsModal({
  sourceType,
  onSubmit,
  onCancel,
  submitting,
}: {
  sourceType: SourceType;
  onSubmit: (creds: Record<string, string>) => void;
  onCancel: () => void;
  submitting: boolean;
}) {
  const fields = CREDENTIAL_FIELDS[sourceType] ?? [];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, ""]))
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Skip fields whose label contains "(optional)" — they are not required
    const requiredFields = fields.filter((f) => !f.label.toLowerCase().includes("optional"));
    const missing = requiredFields.filter((f) => !values[f.key]?.trim());
    if (missing.length) {
      toast.error(`Please fill in: ${missing.map((f) => f.label).join(", ")}`);
      return;
    }
    onSubmit(values);
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 50,
      background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem",
    }}>
      <div style={{
        background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: "1rem", padding: "1.5rem", width: "100%", maxWidth: "480px",
        boxShadow: "0 25px 50px rgba(0,0,0,0.5)",
      }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
            <span style={{ fontSize: "1.5rem" }}>{SOURCE_TYPE_ICONS[sourceType]}</span>
            <div>
              <h2 style={{ color: "#f1f5f9", fontSize: "1rem", fontWeight: 600, margin: 0 }}>
                Connect {SOURCE_TYPE_LABELS[sourceType]}
              </h2>
              <p style={{ color: "#64748b", fontSize: "0.75rem", margin: "0.125rem 0 0" }}>
                Enter your credentials to start syncing
              </p>
            </div>
          </div>
          <button onClick={onCancel} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", padding: "0.25rem" }}>
            <X size={18} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {fields.map((field) => (
              <div key={field.key}>
                <label style={{ display: "block", color: "#94a3b8", fontSize: "0.8125rem", fontWeight: 500, marginBottom: "0.375rem" }}>
                  {field.label}
                </label>
                <input
                  type={field.type ?? "text"}
                  value={values[field.key]}
                  onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                  placeholder={field.placeholder}
                  autoComplete="off"
                  style={{
                    width: "100%", boxSizing: "border-box",
                    background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: "0.5rem", padding: "0.625rem 0.75rem",
                    color: "#f1f5f9", fontSize: "0.875rem", outline: "none",
                  }}
                />
                {field.hint && (
                  <p style={{ color: "#475569", fontSize: "0.75rem", marginTop: "0.25rem" }}>{field.hint}</p>
                )}
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: "0.75rem", marginTop: "1.5rem" }}>
            <button
              type="button"
              onClick={onCancel}
              style={{
                flex: 1, padding: "0.625rem", borderRadius: "0.5rem",
                background: "none", border: "1px solid rgba(255,255,255,0.1)",
                color: "#94a3b8", fontSize: "0.875rem", cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              style={{
                flex: 2, padding: "0.625rem", borderRadius: "0.5rem",
                background: submitting ? "#0f766e" : "#14b8a6",
                border: "none", color: "white", fontSize: "0.875rem",
                fontWeight: 600, cursor: submitting ? "not-allowed" : "pointer",
                display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem",
              }}
            >
              {submitting ? "Connecting…" : `Connect ${SOURCE_TYPE_LABELS[sourceType]}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const STATUS_ICON: Record<StatusVariant, React.ReactNode> = {
  active:       <CheckCircle2 className="h-4 w-4 text-green-500" />,
  pending:      <Clock className="h-4 w-4 text-yellow-500" />,
  error:        <XCircle className="h-4 w-4 text-red-500" />,
  disconnected: <AlertCircle className="h-4 w-4 text-gray-400" />,
};

const STATUS_LABEL: Record<StatusVariant, string> = {
  active:       "Connected",
  pending:      "Syncing...",
  error:        "Error",
  disconnected: "Disconnected",
};

const COLLAB_SOURCES: { type: SourceType; description: string }[] = [
  { type: "slack",        description: "Messages, channels, threads" },
  { type: "jira",         description: "Issues, sprints, epics" },
  { type: "google_drive", description: "Docs, sheets, slides" },
  { type: "zendesk",      description: "Tickets, comments, CSAT" },
  { type: "github",       description: "Issues, PRs, releases" },
  { type: "bitbucket",    description: "Repos, PRs, commit history" },
  { type: "hubspot",      description: "Deals, contacts, companies" },
];

const LOG_SOURCES: { type: SourceType; description: string }[] = [
  { type: "railway",       description: "Deployment logs & crash reports" },
  { type: "elasticsearch", description: "Search & log analytics (ELK stack)" },
  { type: "datadog",       description: "Metrics, logs, APM traces" },
  { type: "cloudwatch",    description: "AWS logs, alarms, metrics" },
  { type: "splunk",        description: "Machine data, SIEM events" },
  { type: "azure_monitor", description: "Azure activity & diagnostic logs" },
  { type: "gcp_logging",   description: "Stackdriver / Cloud Logging" },
];

const ALL_SOURCES = [...COLLAB_SOURCES, ...LOG_SOURCES];

function SourceGrid({
  sources,
  getIntegration,
  connecting,
  syncing,
  syncProgress,
  onConnect,
  onDisconnect,
  onSync,
}: {
  sources: { type: SourceType; description: string }[];
  getIntegration: (t: SourceType) => Integration | undefined;
  connecting: SourceType | null;
  syncing: string | null;
  syncProgress: Record<string, number>;
  onConnect: (t: SourceType) => void;
  onDisconnect: (id: string, t: SourceType) => void;
  onSync: (id: string, t: SourceType) => void;
}) {
  const statusColors: Record<StatusVariant, string> = {
    active: "#22c55e", pending: "#eab308", error: "#ef4444", disconnected: "#64748b",
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1rem" }}>
      {sources.map(({ type, description }) => {
        const existing = getIntegration(type);
        const isActive = existing?.status === "active";
        const isSyncing = !!(existing && (syncProgress[existing.id] !== undefined || existing.status === "pending"));
        const isPending = connecting === type || syncing === existing?.id || isSyncing;
        const statusVariant = (existing?.status ?? "disconnected") as StatusVariant;
        const recordCount = existing ? (syncProgress[existing.id] ?? existing.total_records ?? 0) : 0;

        return (
          <div
            key={type}
            style={{
              background: "#1e293b",
              border: `1px solid ${isActive ? "rgba(34,197,94,0.3)" : "rgba(255,255,255,0.07)"}`,
              borderRadius: "0.75rem", padding: "1.25rem",
              display: "flex", flexDirection: "column", gap: "1rem",
            }}
          >
            {/* Title row */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: "0.75rem" }}>
              <span style={{ fontSize: "1.5rem", lineHeight: 1 }}>{SOURCE_TYPE_ICONS[type]}</span>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                  <h3 style={{ color: "#f1f5f9", fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>
                    {SOURCE_TYPE_LABELS[type]}
                  </h3>
                  {existing && (
                    <span style={{
                      fontSize: "0.7rem", padding: "0.125rem 0.5rem", borderRadius: "9999px",
                      background: `${statusColors[statusVariant]}20`,
                      color: statusColors[statusVariant], fontWeight: 600,
                    }}>
                      {STATUS_LABEL[statusVariant]}
                    </span>
                  )}
                </div>
                <p style={{ color: "#64748b", fontSize: "0.75rem", marginTop: "0.25rem" }}>{description}</p>
              </div>
            </div>

            {isSyncing && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.375rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ color: "#eab308", fontSize: "0.75rem" }}>Syncing…</span>
                  <span style={{ color: "#64748b", fontSize: "0.75rem" }}>{recordCount} records indexed</span>
                </div>
                <div style={{ height: "4px", borderRadius: "9999px", background: "rgba(255,255,255,0.07)", overflow: "hidden" }}>
                  <div style={{
                    height: "100%", borderRadius: "9999px", background: "#eab308",
                    width: "40%",
                    animation: "indeterminate 1.5s ease-in-out infinite",
                  }} />
                </div>
              </div>
            )}
            {!isSyncing && existing && (
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                {existing.last_synced_at ? (
                  <p style={{ color: "#475569", fontSize: "0.75rem", margin: 0 }}>
                    Last synced {formatDistanceToNow(new Date(existing.last_synced_at), { addSuffix: true })}
                  </p>
                ) : <span />}
                {recordCount > 0 && (
                  <span style={{ color: "#475569", fontSize: "0.75rem" }}>{recordCount} records</span>
                )}
              </div>
            )}

            {/* Action buttons */}
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "auto" }}>
              {existing ? (
                <>
                  <button
                    onClick={() => onSync(existing.id, type)}
                    disabled={!!isPending}
                    style={{
                      flex: 1, fontSize: "0.8125rem", padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
                      border: "1px solid rgba(255,255,255,0.1)", background: "none",
                      color: isPending ? "#475569" : "#94a3b8", cursor: isPending ? "not-allowed" : "pointer",
                    }}
                  >
                    {syncing === existing.id ? "Syncing…" : "Sync now"}
                  </button>
                  <button
                    onClick={() => onDisconnect(existing.id, type)}
                    style={{
                      fontSize: "0.8125rem", padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
                      border: "1px solid rgba(239,68,68,0.3)", background: "none",
                      color: "#f87171", cursor: "pointer",
                    }}
                  >
                    Disconnect
                  </button>
                </>
              ) : (
                <button
                  onClick={() => onConnect(type)}
                  disabled={!!connecting}
                  style={{
                    flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: "0.375rem",
                    fontSize: "0.8125rem", padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
                    background: connecting === type ? "#0f766e" : "#14b8a6",
                    border: "none", color: "white", fontWeight: 600,
                    cursor: connecting ? "not-allowed" : "pointer", opacity: connecting && connecting !== type ? 0.5 : 1,
                  }}
                >
                  <Plug size={13} />
                  {connecting === type ? "Connecting…" : "Connect"}
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function IntegrationsPage() {
  const { getToken } = useAuth();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading]           = useState(true);
  const [connecting, setConnecting]     = useState<SourceType | null>(null);
  const [syncing, setSyncing]           = useState<string | null>(null);
  const [pendingConnect, setPendingConnect] = useState<SourceType | null>(null);
  // Track integration IDs actively syncing (DB status = pending) + their live record count
  const [syncProgress, setSyncProgress] = useState<Record<string, number>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getToken();
      setIntegrations(await integrationsApi.list(token!));
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to load integrations");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => { load(); }, [load]);

  // Poll status for any integration that is pending
  useEffect(() => {
    const pendingIds = integrations
      .filter((i) => i.status === "pending" || syncProgress[i.id] !== undefined)
      .map((i) => i.id);

    if (pendingIds.length === 0) return;

    const interval = setInterval(async () => {
      const token = await getToken();
      if (!token) return;
      let anyStillPending = false;
      await Promise.all(
        pendingIds.map(async (id) => {
          try {
            const s = await integrationsApi.getStatus(id, token);
            setSyncProgress((prev) => ({ ...prev, [id]: s.total_records }));
            if (s.status === "pending") {
              anyStillPending = true;
            } else {
              // Sync finished — refresh list and clear progress entry
              setIntegrations((prev) =>
                prev.map((i) =>
                  i.id === id ? { ...i, status: s.status as Integration["status"], total_records: s.total_records } : i
                )
              );
              setSyncProgress((prev) => { const n = { ...prev }; delete n[id]; return n; });
              if (s.status === "active") toast.success(`Sync complete — ${s.total_records} records indexed`);
              if (s.status === "error") toast.error(s.error_message ?? "Sync failed");
            }
          } catch { /* ignore transient fetch errors */ }
        })
      );
      if (!anyStillPending) clearInterval(interval);
    }, 3000);

    return () => clearInterval(interval);
  }, [integrations, getToken]); // eslint-disable-line react-hooks/exhaustive-deps

  const getIntegration = (type: SourceType) =>
    integrations.find((i) => i.source_type === type);

  // Step 1: clicking "Connect" opens the credentials modal
  const handleConnect = (type: SourceType) => {
    setPendingConnect(type);
  };

  // Step 2: modal submits credentials → actual API call
  const handleCredentialsSubmit = async (credentials: Record<string, string>) => {
    if (!pendingConnect) return;
    const type = pendingConnect;
    setConnecting(type);
    try {
      const token = await getToken();
      const integration = await integrationsApi.connect(token!, type, credentials);
      toast.success(`${SOURCE_TYPE_LABELS[type]} connected — syncing now…`);
      setPendingConnect(null);
      // Mark as pending immediately so polling starts
      setSyncProgress((prev) => ({ ...prev, [integration.id]: 0 }));
      await load();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to connect";
      toast.error(msg);
    } finally {
      setConnecting(null);
    }
  };

  const handleDisconnect = async (id: string, type: SourceType) => {
    if (!confirm(`Disconnect ${SOURCE_TYPE_LABELS[type]}? Synced data will remain.`)) return;
    try {
      const token = await getToken();
      await integrationsApi.disconnect(id, token!);
      toast.success("Integration disconnected");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to disconnect");
    }
  };

  const handleSync = async (id: string, type: SourceType) => {
    setSyncing(id);
    try {
      const token = await getToken();
      await integrationsApi.triggerSync(id, token!);
      toast.success(`${SOURCE_TYPE_LABELS[type]} sync triggered`);
      // Mark as pending immediately so polling starts
      setSyncProgress((prev) => ({ ...prev, [id]: 0 }));
      setIntegrations((prev) => prev.map((i) => i.id === id ? { ...i, status: "pending" } : i));
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(null);
    }
  };

  const sharedProps = {
    getIntegration,
    connecting,
    syncing,
    syncProgress,
    onConnect: handleConnect,
    onDisconnect: handleDisconnect,
    onSync: handleSync,
  };

  const connectedCount = integrations.filter((i) => i.status === "active").length;

  return (
    <AppShell>
      <style>{`
        @keyframes indeterminate {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(350%); }
        }
      `}</style>
      {/* Credentials modal */}
      {pendingConnect && (
        <CredentialsModal
          sourceType={pendingConnect}
          onSubmit={handleCredentialsSubmit}
          onCancel={() => setPendingConnect(null)}
          submitting={connecting === pendingConnect}
        />
      )}

      <div style={{ padding: "1.5rem", maxWidth: "56rem", margin: "0 auto", width: "100%", display: "flex", flexDirection: "column", gap: "2rem" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h1 style={{ color: "#f1f5f9", fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>Integrations</h1>
            <p style={{ color: "#64748b", fontSize: "0.875rem", marginTop: "0.25rem" }}>
              Connect your tools to start ingesting operational data
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            style={{
              display: "flex", alignItems: "center", gap: "0.375rem",
              fontSize: "0.875rem", padding: "0.5rem 0.75rem", borderRadius: "0.5rem",
              border: "1px solid rgba(255,255,255,0.1)", background: "none",
              color: "#94a3b8", cursor: "pointer",
            }}
          >
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
            Refresh
          </button>
        </div>

        {/* Stats */}
        {!loading && (
          <div style={{ display: "flex", gap: "1.5rem", fontSize: "0.875rem" }}>
            <span style={{ color: "#64748b" }}>
              <span style={{ fontWeight: 600, color: "#22c55e" }}>{connectedCount}</span> connected
            </span>
            <span style={{ color: "#64748b" }}>
              <span style={{ fontWeight: 600, color: "#f1f5f9" }}>{ALL_SOURCES.length - connectedCount}</span> available
            </span>
          </div>
        )}

        {/* Collaboration section */}
        <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <div>
            <h2 style={{ color: "#f1f5f9", fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>Collaboration &amp; Ticketing</h2>
            <p style={{ color: "#64748b", fontSize: "0.75rem", marginTop: "0.25rem" }}>
              Messages, issues, documents, and customer support data
            </p>
          </div>
          <SourceGrid sources={COLLAB_SOURCES} {...sharedProps} />
        </section>

        {/* Log / observability section */}
        <section style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <div>
            <h2 style={{ color: "#f1f5f9", fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>Log &amp; Observability Platforms</h2>
            <p style={{ color: "#64748b", fontSize: "0.75rem", marginTop: "0.25rem" }}>
              Connect log systems to power AI-assisted incident investigation and root cause analysis
            </p>
          </div>
          <SourceGrid sources={LOG_SOURCES} {...sharedProps} />
        </section>

        {/* Info banner */}
        <div style={{
          background: "rgba(20,184,166,0.08)", border: "1px solid rgba(20,184,166,0.25)",
          borderRadius: "0.75rem", padding: "1rem", fontSize: "0.875rem",
        }}>
          <p style={{ color: "#2dd4bf", fontWeight: 600, margin: "0 0 0.25rem" }}>How data ingestion works</p>
          <p style={{ color: "#64748b", margin: 0, lineHeight: 1.6 }}>
            After connecting, OpsLens AI syncs your data on a schedule. Documents and log entries are
            chunked, embedded, and stored in Qdrant for RAG queries and incident signal correlation.
            Log platforms are especially powerful during incident investigations — correlated signals
            from logs, commits, tickets, and Slack are used to generate root cause analysis automatically.
          </p>
        </div>
      </div>
    </AppShell>
  );
}
