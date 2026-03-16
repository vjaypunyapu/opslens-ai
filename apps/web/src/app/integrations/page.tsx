"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { RefreshCw, Plug, CheckCircle2, XCircle, Clock, AlertCircle } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { cn } from "@/lib/utils";
import { SOURCE_TYPE_ICONS, SOURCE_TYPE_LABELS } from "@/lib/utils";
import { Integration, SourceType } from "@/types";
import { integrationsApi } from "@/lib/api";

type StatusVariant = "active" | "pending" | "error" | "disconnected";

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
  { type: "hubspot",      description: "Deals, contacts, companies" },
];

const LOG_SOURCES: { type: SourceType; description: string }[] = [
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
  onConnect,
  onDisconnect,
  onSync,
}: {
  sources: { type: SourceType; description: string }[];
  getIntegration: (t: SourceType) => Integration | undefined;
  connecting: SourceType | null;
  syncing: string | null;
  onConnect: (t: SourceType) => void;
  onDisconnect: (id: string, t: SourceType) => void;
  onSync: (id: string, t: SourceType) => void;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {sources.map(({ type, description }) => {
        const existing = getIntegration(type);
        const isActive = existing?.status === "active";
        const isPending = connecting === type || syncing === existing?.id;

        return (
          <div
            key={type}
            className={cn(
              "bg-white border rounded-xl p-5 flex flex-col gap-4 transition-shadow hover:shadow-md",
              isActive ? "border-green-200" : "border-gray-200",
            )}
          >
            <div className="flex items-start gap-3">
              <span className="text-2xl">{SOURCE_TYPE_ICONS[type]}</span>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-gray-800">
                    {SOURCE_TYPE_LABELS[type]}
                  </h3>
                  {existing && (
                    <div className="flex items-center gap-1">
                      {STATUS_ICON[existing.status as StatusVariant]}
                      <span className="text-xs text-gray-500">
                        {STATUS_LABEL[existing.status as StatusVariant]}
                      </span>
                    </div>
                  )}
                </div>
                <p className="text-xs text-gray-400 mt-0.5">{description}</p>
              </div>
            </div>

            {existing?.last_synced_at && (
              <p className="text-xs text-gray-400">
                Last synced{" "}
                {formatDistanceToNow(new Date(existing.last_synced_at), { addSuffix: true })}
              </p>
            )}

            <div className="flex gap-2 mt-auto">
              {existing ? (
                <>
                  <button
                    onClick={() => onSync(existing.id, type)}
                    disabled={!!isPending}
                    className="flex-1 text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:border-brand-teal hover:text-brand-teal transition-colors disabled:opacity-50"
                  >
                    {syncing === existing.id ? "Syncing..." : "Sync now"}
                  </button>
                  <button
                    onClick={() => onDisconnect(existing.id, type)}
                    className="text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-red-400 hover:border-red-300 hover:text-red-600 transition-colors"
                  >
                    Disconnect
                  </button>
                </>
              ) : (
                <button
                  onClick={() => onConnect(type)}
                  disabled={!!connecting}
                  className="flex-1 flex items-center justify-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-brand-navy text-white hover:bg-brand-blue transition-colors disabled:opacity-50"
                >
                  <Plug className="h-3.5 w-3.5" />
                  {connecting === type ? "Connecting..." : "Connect"}
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

  const getIntegration = (type: SourceType) =>
    integrations.find((i) => i.source_type === type);

  const handleConnect = async (type: SourceType) => {
    setConnecting(type);
    try {
      const token = await getToken();
      await integrationsApi.connect(token!, type, {});
      toast.success(`${SOURCE_TYPE_LABELS[type]} connected - first sync starting...`);
      await load();
    } catch {
      toast.error("Failed to connect. Check your credentials.");
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
      setTimeout(load, 2000);
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
    onConnect: handleConnect,
    onDisconnect: handleDisconnect,
    onSync: handleSync,
  };

  const connectedCount = integrations.filter((i) => i.status === "active").length;

  return (
    <AppShell>
      <div className="p-6 max-w-4xl mx-auto w-full space-y-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Integrations</h1>
            <p className="text-sm text-gray-400 mt-0.5">
              Connect your tools to start ingesting operational data
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:border-gray-300 transition-colors"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </button>
        </div>

        {!loading && (
          <div className="flex gap-6 text-sm">
            <span className="text-gray-400">
              <span className="font-semibold text-green-600">{connectedCount}</span>{" "}connected
            </span>
            <span className="text-gray-400">
              <span className="font-semibold text-gray-700">
                {ALL_SOURCES.length - connectedCount}
              </span>{" "}available
            </span>
          </div>
        )}

        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-700">Collaboration &amp; Ticketing</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Messages, issues, documents, and customer support data
            </p>
          </div>
          <SourceGrid sources={COLLAB_SOURCES} {...sharedProps} />
        </section>

        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-700">Log &amp; Observability Platforms</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Connect log systems to power AI-assisted incident investigation and root cause analysis
            </p>
          </div>
          <SourceGrid sources={LOG_SOURCES} {...sharedProps} />
        </section>

        <div className="bg-brand-ice border border-brand-teal/30 rounded-xl p-4 text-sm text-brand-blue">
          <p className="font-medium mb-1">How data ingestion works</p>
          <p className="text-brand-blue/70">
            After connecting, OpsLens AI syncs your data on a schedule. Documents and log entries are
            chunked, embedded, and stored in Qdrant for RAG queries and incident signal correlation.
            Log platforms are especially powerful during incident investigations: correlated signals
            from logs, commits, tickets, and Slack are used to generate root cause analysis automatically.
          </p>
        </div>
      </div>
    </AppShell>
  );
}
