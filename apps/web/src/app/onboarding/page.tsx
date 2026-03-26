"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  CheckCircle2,
  Circle,
  ChevronRight,
  ChevronLeft,
  Plug,
  Shield,
  Bell,
  Zap,
  ExternalLink,
  Loader2,
  AlertCircle,
  Copy,
  Check,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface Step {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  required: boolean;
}

interface RoutingRuleForm {
  team_name: string;
  error_patterns: string;
  service_patterns: string;
  slack_webhook: string;
  email: string;
  priority: number;
}

interface SimResult {
  status: string;
  enrich_task_id?: string;
  error_signature?: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const STEPS: Step[] = [
  {
    id: "connect",
    title: "Connect your data sources",
    description: "Link Jira, GitHub, and Slack so OpsLens can pull historical context for incident briefs.",
    icon: <Plug size={20} />,
    required: true,
  },
  {
    id: "routing",
    title: "Set up alert routing",
    description: "Tell OpsLens which errors go to which team. You can always add more rules later.",
    icon: <Bell size={20} />,
    required: true,
  },
  {
    id: "webhook",
    title: "Configure log ingestion",
    description: "Push errors directly into OpsLens from your log shipper for sub-second alerting.",
    icon: <Zap size={20} />,
    required: false,
  },
  {
    id: "test",
    title: "Run your first simulation",
    description: "Fire a synthetic alert to verify the full pipeline end-to-end before going live.",
    icon: <Shield size={20} />,
    required: false,
  },
];

const INTEGRATION_CARDS = [
  {
    id: "jira",
    name: "Jira",
    logo: "https://cdn.worldvectorlogo.com/logos/jira-1.svg",
    description: "Pull tickets, comments, and sprint history",
    docsUrl: "https://docs.opslens.ai/integrations/jira",
  },
  {
    id: "github",
    name: "GitHub",
    logo: "https://cdn.worldvectorlogo.com/logos/github-icon-1.svg",
    description: "Sync PRs, commits, and release notes",
    docsUrl: "https://docs.opslens.ai/integrations/github",
  },
  {
    id: "slack",
    name: "Slack",
    logo: "https://cdn.worldvectorlogo.com/logos/slack-new-logo.svg",
    description: "Index incident threads and postmortems",
    docsUrl: "https://docs.opslens.ai/integrations/slack",
  },
];

// ─── Main Component ───────────────────────────────────────────────────────────

export default function OnboardingPage() {
  const router = useRouter();
  const [currentStep, setCurrentStep] = useState(0);
  const [completedSteps, setCompletedSteps] = useState<Set<string>>(new Set());
  const [connectedIntegrations, setConnectedIntegrations] = useState<Set<string>>(new Set());
  const [checkingIntegrations, setCheckingIntegrations] = useState(true);
  const [copied, setCopied] = useState(false);

  // Routing rule form
  const [ruleForm, setRuleForm] = useState<RoutingRuleForm>({
    team_name: "",
    error_patterns: "",
    service_patterns: "",
    slack_webhook: "",
    email: "",
    priority: 10,
  });
  const [savingRule, setSavingRule] = useState(false);
  const [ruleError, setRuleError] = useState<string | null>(null);
  const [ruleSaved, setRuleSaved] = useState(false);

  // Simulation
  const [simError, setSimError] = useState("PaymentError: Stripe API timeout after 30s");
  const [simService, setSimService] = useState("payment-service");
  const [simWebhook, setSimWebhook] = useState("");
  const [running, setRunning] = useState(false);
  const [simResult, setSimResult] = useState<SimResult | null>(null);

  // Load integration status on mount
  useEffect(() => {
    fetch("/api/v1/integrations")
      .then((r) => r.json())
      .then((data: { source_type: string; status: string }[]) => {
        const connected = new Set(
          data.filter((i) => i.status === "active").map((i) => i.source_type)
        );
        setConnectedIntegrations(connected);
        if (connected.size > 0) {
          setCompletedSteps((prev) => new Set([...prev, "connect"]));
        }
      })
      .catch(() => {})
      .finally(() => setCheckingIntegrations(false));
  }, []);

  // ── Helpers ─────────────────────────────────────────────────────────────────

  const markComplete = (stepId: string) => {
    setCompletedSteps((prev) => new Set([...prev, stepId]));
  };

  const progress = (completedSteps.size / STEPS.length) * 100;

  const ingestUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/api/v1/log-ops/ingest`
      : "https://api.opslens.ai/api/v1/log-ops/ingest";

  const curlSnippet = `curl -X POST ${ingestUrl} \\
  -H "Authorization: Bearer <YOUR_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "service_name": "payment-service",
    "error_message": "PaymentError: Stripe API timeout",
    "error_count": 5,
    "severity": "p1"
  }'`;

  const handleCopy = () => {
    navigator.clipboard.writeText(curlSnippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ── Save routing rule ────────────────────────────────────────────────────────

  const handleSaveRule = async () => {
    if (!ruleForm.team_name || !ruleForm.slack_webhook) {
      setRuleError("Team name and Slack webhook are required.");
      return;
    }
    setSavingRule(true);
    setRuleError(null);
    try {
      const res = await fetch("/api/v1/log-ops/routing-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_name: ruleForm.team_name,
          error_patterns: ruleForm.error_patterns
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          service_patterns: ruleForm.service_patterns
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          slack_webhook: ruleForm.slack_webhook,
          email_recipients: ruleForm.email ? [ruleForm.email] : [],
          priority: ruleForm.priority,
          is_active: true,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        setRuleError(err.detail || "Failed to save rule.");
      } else {
        setRuleSaved(true);
        markComplete("routing");
      }
    } catch (e) {
      setRuleError("Network error — please try again.");
    } finally {
      setSavingRule(false);
    }
  };

  // ── Run simulation ───────────────────────────────────────────────────────────

  const handleSimulate = async () => {
    setRunning(true);
    setSimResult(null);
    try {
      const res = await fetch("/api/v1/log-ops/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          service_name: simService,
          error_message: simError,
          error_count: 5,
          severity: "p1",
          webhook_url: simWebhook || undefined,
        }),
      });
      const data = await res.json();
      setSimResult(data);
      markComplete("test");
    } catch (e) {
      setSimResult({ status: "error" });
    } finally {
      setRunning(false);
    }
  };

  // ── Step content ─────────────────────────────────────────────────────────────

  const renderStep = () => {
    const step = STEPS[currentStep];

    if (step.id === "connect") {
      return (
        <div className="space-y-4">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Connect at least one source so OpsLens can enrich your incident briefs with
            historical context — Jira tickets, GitHub PRs, and Slack threads from any date.
          </p>
          {checkingIntegrations ? (
            <div className="flex items-center gap-2" style={{ color: "var(--text-secondary)" }}>
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">Checking existing connections…</span>
            </div>
          ) : (
            <div className="grid gap-3">
              {INTEGRATION_CARDS.map((card) => {
                const isConnected = connectedIntegrations.has(card.id);
                return (
                  <div
                    key={card.id}
                    className="flex items-center justify-between rounded-lg border p-4"
                    style={{
                      background: "var(--bg-elevated)",
                      borderColor: isConnected ? "var(--accent)" : "var(--border)",
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <img src={card.logo} alt={card.name} className="h-7 w-7 object-contain" />
                      <div>
                        <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                          {card.name}
                        </p>
                        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                          {card.description}
                        </p>
                      </div>
                    </div>
                    {isConnected ? (
                      <span
                        className="flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium"
                        style={{ background: "#16a34a22", color: "#4ade80" }}
                      >
                        <CheckCircle2 size={12} />
                        Connected
                      </span>
                    ) : (
                      <a
                        href="/integrations"
                        className="flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-80"
                        style={{ background: "var(--accent)", color: "#fff" }}
                      >
                        Connect
                        <ExternalLink size={11} />
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {connectedIntegrations.size > 0 && (
            <p className="text-xs" style={{ color: "#4ade80" }}>
              ✓ {connectedIntegrations.size} integration{connectedIntegrations.size > 1 ? "s" : ""} connected.
              OpsLens is syncing your data in the background — this usually takes 2–5 minutes.
            </p>
          )}
          <button
            onClick={() => {
              if (connectedIntegrations.size > 0) markComplete("connect");
              setCurrentStep(1);
            }}
            className="mt-2 flex items-center gap-1 text-sm font-medium transition-opacity hover:opacity-80"
            style={{ color: "var(--accent)" }}
          >
            {connectedIntegrations.size === 0 ? "Skip for now" : "Continue"}
            <ChevronRight size={14} />
          </button>
        </div>
      );
    }

    if (step.id === "routing") {
      return (
        <div className="space-y-4">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Create your first routing rule. OpsLens will match incoming errors against these
            patterns and send your team an enriched Slack alert.
          </p>
          {ruleSaved ? (
            <div
              className="rounded-lg border p-4 text-sm"
              style={{ borderColor: "var(--accent)", background: "#16a34a11", color: "#4ade80" }}
            >
              <CheckCircle2 size={16} className="inline mr-2" />
              Routing rule saved! Your team will receive alerts matching those patterns.
            </div>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                    Team name *
                  </label>
                  <input
                    value={ruleForm.team_name}
                    onChange={(e) => setRuleForm({ ...ruleForm, team_name: e.target.value })}
                    placeholder="Payments Team"
                    className="w-full rounded-md border px-3 py-2 text-sm outline-none focus:ring-1"
                    style={{
                      background: "var(--bg-elevated)",
                      borderColor: "var(--border)",
                      color: "var(--text-primary)",
                    }}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                    Priority (1 = highest)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={999}
                    value={ruleForm.priority}
                    onChange={(e) => setRuleForm({ ...ruleForm, priority: +e.target.value })}
                    className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                    style={{
                      background: "var(--bg-elevated)",
                      borderColor: "var(--border)",
                      color: "var(--text-primary)",
                    }}
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                  Error patterns (comma-separated keywords or regex)
                </label>
                <input
                  value={ruleForm.error_patterns}
                  onChange={(e) => setRuleForm({ ...ruleForm, error_patterns: e.target.value })}
                  placeholder="PaymentError, StripeTimeout, CardDeclined"
                  className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                  style={{
                    background: "var(--bg-elevated)",
                    borderColor: "var(--border)",
                    color: "var(--text-primary)",
                  }}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                  Service patterns (comma-separated container / service names)
                </label>
                <input
                  value={ruleForm.service_patterns}
                  onChange={(e) => setRuleForm({ ...ruleForm, service_patterns: e.target.value })}
                  placeholder="payment-service, billing-worker"
                  className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                  style={{
                    background: "var(--bg-elevated)",
                    borderColor: "var(--border)",
                    color: "var(--text-primary)",
                  }}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                  Slack webhook URL *
                </label>
                <input
                  value={ruleForm.slack_webhook}
                  onChange={(e) => setRuleForm({ ...ruleForm, slack_webhook: e.target.value })}
                  placeholder="https://hooks.slack.com/services/…"
                  className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                  style={{
                    background: "var(--bg-elevated)",
                    borderColor: "var(--border)",
                    color: "var(--text-primary)",
                  }}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                  On-call email (optional)
                </label>
                <input
                  type="email"
                  value={ruleForm.email}
                  onChange={(e) => setRuleForm({ ...ruleForm, email: e.target.value })}
                  placeholder="oncall@yourcompany.com"
                  className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                  style={{
                    background: "var(--bg-elevated)",
                    borderColor: "var(--border)",
                    color: "var(--text-primary)",
                  }}
                />
              </div>
              {ruleError && (
                <p className="flex items-center gap-1 text-xs" style={{ color: "#f87171" }}>
                  <AlertCircle size={12} />
                  {ruleError}
                </p>
              )}
              <button
                onClick={handleSaveRule}
                disabled={savingRule}
                className="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-80 disabled:opacity-50"
                style={{ background: "var(--accent)" }}
              >
                {savingRule && <Loader2 size={14} className="animate-spin" />}
                Save routing rule
              </button>
            </div>
          )}
          <a
            href="/routing-rules"
            className="mt-1 flex items-center gap-1 text-xs transition-opacity hover:opacity-70"
            style={{ color: "var(--text-secondary)" }}
          >
            Manage all routing rules
            <ExternalLink size={11} />
          </a>
        </div>
      );
    }

    if (step.id === "webhook") {
      return (
        <div className="space-y-4">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Instead of waiting for the 5-minute polling cycle, push errors directly from your
            log shipper for immediate alerting.
          </p>
          <div className="rounded-lg border p-4 space-y-2" style={{ borderColor: "var(--border)", background: "var(--bg-elevated)" }}>
            <p className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
              Endpoint
            </p>
            <code className="block text-xs font-mono" style={{ color: "var(--accent)" }}>
              POST {ingestUrl}
            </code>
          </div>
          <div className="relative rounded-lg border" style={{ borderColor: "var(--border)", background: "#0f172a" }}>
            <pre className="overflow-x-auto p-4 text-xs text-green-400">{curlSnippet}</pre>
            <button
              onClick={handleCopy}
              className="absolute right-3 top-3 flex items-center gap-1 rounded px-2 py-1 text-xs transition-opacity hover:opacity-80"
              style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)" }}
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
          <div className="space-y-2 text-xs" style={{ color: "var(--text-secondary)" }}>
            <p className="font-medium" style={{ color: "var(--text-primary)" }}>
              Compatible log shippers:
            </p>
            <div className="grid grid-cols-3 gap-2">
              {["Fluentd", "Logstash", "Vector", "Datadog Webhook", "CloudWatch Alarm", "Custom scripts"].map((s) => (
                <span
                  key={s}
                  className="rounded px-2 py-1 text-center"
                  style={{ background: "var(--bg-elevated)" }}
                >
                  {s}
                </span>
              ))}
            </div>
          </div>
          <button
            onClick={() => markComplete("webhook")}
            className="flex items-center gap-1 text-sm font-medium transition-opacity hover:opacity-80"
            style={{ color: "var(--accent)" }}
          >
            Mark as configured
            <CheckCircle2 size={14} />
          </button>
        </div>
      );
    }

    if (step.id === "test") {
      return (
        <div className="space-y-4">
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Fire a synthetic error through the full pipeline — enrichment, Slack alert, and RRT
            brief — before any real incident happens.
          </p>
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                Service name
              </label>
              <input
                value={simService}
                onChange={(e) => setSimService(e.target.value)}
                className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                Error message
              </label>
              <input
                value={simError}
                onChange={(e) => setSimError(e.target.value)}
                className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                Slack webhook override (optional — uses your routing rules if blank)
              </label>
              <input
                value={simWebhook}
                onChange={(e) => setSimWebhook(e.target.value)}
                placeholder="https://hooks.slack.com/services/…"
                className="w-full rounded-md border px-3 py-2 text-sm outline-none"
                style={{ background: "var(--bg-elevated)", borderColor: "var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <button
              onClick={handleSimulate}
              disabled={running}
              className="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-80 disabled:opacity-50"
              style={{ background: "var(--accent)" }}
            >
              {running && <Loader2 size={14} className="animate-spin" />}
              {running ? "Firing simulation…" : "Run simulation"}
            </button>
          </div>
          {simResult && (
            <div
              className="rounded-lg border p-4 text-sm space-y-1"
              style={{
                borderColor: simResult.status === "dispatched" ? "var(--accent)" : "#f87171",
                background: simResult.status === "dispatched" ? "#16a34a11" : "#f8717111",
              }}
            >
              {simResult.status === "dispatched" ? (
                <>
                  <p style={{ color: "#4ade80" }}>
                    <CheckCircle2 size={14} className="inline mr-1" />
                    Simulation dispatched successfully!
                  </p>
                  <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                    Task ID: {simResult.enrich_task_id}
                  </p>
                  <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                    Check your Slack channel and{" "}
                    <a href="/rrt-briefs" className="underline" style={{ color: "var(--accent)" }}>
                      RRT Briefs
                    </a>{" "}
                    in ~30 seconds.
                  </p>
                </>
              ) : (
                <p style={{ color: "#f87171" }}>
                  <AlertCircle size={14} className="inline mr-1" />
                  Simulation failed — check your webhook configuration.
                </p>
              )}
            </div>
          )}
        </div>
      );
    }

    return null;
  };

  // ── Layout ───────────────────────────────────────────────────────────────────

  const allRequiredDone = STEPS.filter((s) => s.required).every((s) => completedSteps.has(s.id));

  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center p-6"
      style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}
    >
      <div className="w-full max-w-2xl">
        {/* Header */}
        <div className="mb-8 text-center">
          <div
            className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl text-white"
            style={{ background: "var(--accent)" }}
          >
            <Zap size={24} />
          </div>
          <h1 className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>
            Welcome to OpsLens AI
          </h1>
          <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
            You're 4 steps away from AI-powered incident response.
          </p>
        </div>

        {/* Progress bar */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              {completedSteps.size} of {STEPS.length} steps complete
            </span>
            <span className="text-xs font-medium" style={{ color: "var(--accent)" }}>
              {Math.round(progress)}%
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full" style={{ background: "var(--border)" }}>
            <div
              className="h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${progress}%`, background: "var(--accent)" }}
            />
          </div>
        </div>

        {/* Step nav */}
        <div className="mb-6 grid grid-cols-4 gap-2">
          {STEPS.map((step, idx) => {
            const done = completedSteps.has(step.id);
            const active = idx === currentStep;
            return (
              <button
                key={step.id}
                onClick={() => setCurrentStep(idx)}
                className="flex flex-col items-center rounded-lg border p-3 text-center transition-colors"
                style={{
                  borderColor: active ? "var(--accent)" : done ? "#4ade8044" : "var(--border)",
                  background: active ? "var(--accent)11" : "var(--bg-surface)",
                }}
              >
                <span
                  className="mb-1"
                  style={{ color: active ? "var(--accent)" : done ? "#4ade80" : "var(--text-secondary)" }}
                >
                  {done ? <CheckCircle2 size={18} /> : step.icon}
                </span>
                <span
                  className="text-xs font-medium leading-tight"
                  style={{ color: active ? "var(--accent)" : done ? "#4ade80" : "var(--text-secondary)" }}
                >
                  {step.title}
                </span>
                {!step.required && (
                  <span className="mt-0.5 text-[10px]" style={{ color: "var(--text-secondary)" }}>
                    optional
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Step card */}
        <div
          className="rounded-xl border p-6"
          style={{ background: "var(--bg-surface)", borderColor: "var(--border)" }}
        >
          <div className="mb-4 flex items-center gap-3">
            <span style={{ color: "var(--accent)" }}>{STEPS[currentStep].icon}</span>
            <div>
              <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
                {STEPS[currentStep].title}
              </h2>
              <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Step {currentStep + 1} of {STEPS.length}
                {!STEPS[currentStep].required && " · optional"}
              </p>
            </div>
          </div>
          {renderStep()}
        </div>

        {/* Navigation */}
        <div className="mt-4 flex items-center justify-between">
          <button
            onClick={() => setCurrentStep((p) => Math.max(0, p - 1))}
            disabled={currentStep === 0}
            className="flex items-center gap-1 rounded-md px-3 py-2 text-sm transition-opacity hover:opacity-80 disabled:opacity-30"
            style={{ color: "var(--text-secondary)", background: "var(--bg-surface)", border: "1px solid var(--border)" }}
          >
            <ChevronLeft size={14} />
            Back
          </button>

          {currentStep < STEPS.length - 1 ? (
            <button
              onClick={() => setCurrentStep((p) => p + 1)}
              className="flex items-center gap-1 rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-80"
              style={{ background: "var(--accent)" }}
            >
              Next
              <ChevronRight size={14} />
            </button>
          ) : (
            <button
              onClick={() => router.push("/dashboard")}
              className="flex items-center gap-1 rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-80"
              style={{ background: allRequiredDone ? "var(--accent)" : "#6b7280" }}
            >
              {allRequiredDone ? "Go to dashboard" : "Skip to dashboard"}
              <ChevronRight size={14} />
            </button>
          )}
        </div>

        {/* Footer */}
        <p className="mt-6 text-center text-xs" style={{ color: "var(--text-secondary)" }}>
          Need help?{" "}
          <a
            href="mailto:support@opslens.ai"
            className="underline hover:opacity-80"
            style={{ color: "var(--accent)" }}
          >
            support@opslens.ai
          </a>{" "}
          · Docs at{" "}
          <a
            href="https://docs.opslens.ai"
            target="_blank"
            rel="noreferrer"
            className="underline hover:opacity-80"
            style={{ color: "var(--accent)" }}
          >
            docs.opslens.ai
          </a>
        </p>
      </div>
    </div>
  );
}
