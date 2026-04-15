"use client";

import { useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { demoApi } from "@/lib/api";

type LatestBriefResult = Awaited<ReturnType<typeof demoApi.latestBrief>>;
type RailwayRawResult = Awaited<ReturnType<typeof demoApi.railwayRaw>>;

export default function DevDiagnosticsPage() {
  const { getToken } = useAuth();
  const [githubResult, setGithubResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [ctxResult, setCtxResult] = useState<Record<string, unknown> | null>(null);
  const [ctxLoading, setCtxLoading] = useState(false);
  const [ctxError, setCtxError] = useState<string | null>(null);

  const [briefResult, setBriefResult] = useState<LatestBriefResult | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [briefError, setBriefError] = useState<string | null>(null);

  const [railwayRaw, setRailwayRaw] = useState<RailwayRawResult | null>(null);
  const [railwayRawLoading, setRailwayRawLoading] = useState(false);
  const [railwayRawError, setRailwayRawError] = useState<string | null>(null);

  const [forceBriefResult, setForceBriefResult] = useState<{ fired: boolean; message?: string; error?: string; sample_lines?: string[] } | null>(null);
  const [forceBriefLoading, setForceBriefLoading] = useState(false);

  async function triggerForceBrief() {
    setForceBriefLoading(true);
    setForceBriefResult(null);
    try {
      const token = await getToken();
      if (!token) throw new Error("Not authenticated");
      const result = await demoApi.forceBrief(token);
      setForceBriefResult(result);
    } catch (e: unknown) {
      setForceBriefResult({ fired: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setForceBriefLoading(false);
    }
  }

  async function fetchRailwayRaw() {
    setRailwayRawLoading(true);
    setRailwayRawError(null);
    setRailwayRaw(null);
    try {
      const token = await getToken();
      if (!token) throw new Error("Not authenticated");
      const result = await demoApi.railwayRaw(token);
      setRailwayRaw(result);
    } catch (e: unknown) {
      setRailwayRawError(e instanceof Error ? e.message : String(e));
    } finally {
      setRailwayRawLoading(false);
    }
  }

  async function fetchLatestBrief() {
    setBriefLoading(true);
    setBriefError(null);
    setBriefResult(null);
    try {
      const token = await getToken();
      if (!token) throw new Error("Not authenticated");
      const result = await demoApi.latestBrief(token);
      setBriefResult(result);
    } catch (e: unknown) {
      setBriefError(e instanceof Error ? e.message : String(e));
    } finally {
      setBriefLoading(false);
    }
  }

  async function runCodeContextTest() {
    setCtxLoading(true);
    setCtxError(null);
    setCtxResult(null);
    try {
      const token = await getToken();
      if (!token) throw new Error("Not authenticated");
      const result = await demoApi.codeContextTest(token);
      setCtxResult(result as Record<string, unknown>);
    } catch (e: unknown) {
      setCtxError(e instanceof Error ? e.message : String(e));
    } finally {
      setCtxLoading(false);
    }
  }

  async function runGithubCheck() {
    setLoading(true);
    setError(null);
    setGithubResult(null);
    try {
      const token = await getToken();
      if (!token) throw new Error("Not authenticated");
      const result = await demoApi.githubCheck(token);
      setGithubResult(result as Record<string, unknown>);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const verdict = githubResult?.verdict as string | undefined;
  const isOk = githubResult?.status === "ok";
  const scopes = githubResult?.accessible_repos as string[] | undefined;

  return (
    <div className="max-w-2xl mx-auto p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dev Diagnostics</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Tools to verify your integrations are configured correctly.
        </p>
      </div>

      {/* GitHub Token Check */}
      <div className="border rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">GitHub Token Check</h2>
            <p className="text-sm text-muted-foreground">
              Verifies your GitHub PAT has <code className="bg-muted px-1 rounded">repo</code> scope
              and can read your source files.
            </p>
          </div>
          <button
            onClick={runGithubCheck}
            disabled={loading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? "Checking…" : "Run Check"}
          </button>
        </div>

        {error && (
          <div className="bg-destructive/10 border border-destructive/30 rounded p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {githubResult && (
          <div className="space-y-3">
            {/* Verdict banner */}
            <div className={`rounded p-3 text-sm font-medium ${isOk ? "bg-green-500/10 border border-green-500/30 text-green-700 dark:text-green-400" : "bg-yellow-500/10 border border-yellow-500/30 text-yellow-700 dark:text-yellow-400"}`}>
              {verdict}
            </div>

            {/* Details table */}
            <table className="w-full text-sm">
              <tbody className="divide-y">
                <Row label="Token valid" value={githubResult.token_valid ? "✅ Yes" : "❌ No"} />
                {githubResult.github_user != null && (
                  <Row label="GitHub user" value={String(githubResult.github_user)} />
                )}
                {Array.isArray(githubResult.scopes) && (
                  <Row
                    label="Scopes"
                    value={(githubResult.scopes as string[]).length > 0
                      ? (githubResult.scopes as string[]).join(", ")
                      : "(none — may be a fine-grained PAT)"}
                  />
                )}
                {githubResult.has_repo_scope != null && (
                  <Row
                    label="Has 'repo' scope"
                    value={githubResult.has_repo_scope ? "✅ Yes" : "❌ No — add 'repo' scope to your PAT"}
                  />
                )}
                {githubResult.repo_count != null && (
                  <Row label="Accessible repos" value={String(githubResult.repo_count)} />
                )}
                {scopes && scopes.length > 0 && (
                  <Row label="Repos (first 10)" value={scopes.join(", ")} />
                )}
                {githubResult.test_file != null && (
                  <Row label="Test file" value={String(githubResult.test_file)} />
                )}
                {githubResult.file_found_in_repo !== undefined && (
                  <Row
                    label="File found in repo"
                    value={githubResult.file_found_in_repo != null
                      ? `✅ ${String(githubResult.file_found_in_repo)}`
                      : `❌ Not found (HTTP ${String(githubResult.file_http_status)})`}
                  />
                )}
              </tbody>
            </table>

            {!!githubResult.scope_warning && (
              <div className="bg-yellow-500/10 border border-yellow-500/30 rounded p-3 text-sm text-yellow-700 dark:text-yellow-400">
                ⚠️ {String(githubResult.scope_warning)}
              </div>
            )}

            {!isOk && !!githubResult.token_valid && (
              <div className="bg-blue-500/10 border border-blue-500/30 rounded p-3 text-sm text-blue-700 dark:text-blue-400">
                <strong>Fix:</strong> Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → generate a new token with the <code className="bg-muted px-1 rounded">repo</code> checkbox checked. Then update it in Integrations → GitHub.
              </div>
            )}

            {!githubResult.token_valid && (
              <div className="bg-blue-500/10 border border-blue-500/30 rounded p-3 text-sm text-blue-700 dark:text-blue-400">
                <strong>Fix:</strong> Your token is invalid or expired. Generate a new classic PAT with <code className="bg-muted px-1 rounded">repo</code> scope at GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic).
              </div>
            )}
          </div>
        )}
      </div>

      {/* Force Brief — bypass Railway, test worker directly */}
      <div className="border-2 border-primary/30 rounded-lg p-6 space-y-4 bg-primary/5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Force Brief (Direct Worker Test)</h2>
            <p className="text-sm text-muted-foreground">
              Fires <code className="bg-muted px-1 rounded">generate_rrt_brief</code> directly with a perfect hardcoded traceback —
              no Railway log fetching. Wait 30s then click Inspect Briefs. If code_frames appear, only the block extractor needs fixing.
            </p>
          </div>
          <button
            onClick={triggerForceBrief}
            disabled={forceBriefLoading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50 whitespace-nowrap"
          >
            {forceBriefLoading ? "Firing…" : "Fire Force Brief"}
          </button>
        </div>

        {forceBriefResult && (
          <div className={`rounded p-3 text-sm ${forceBriefResult.fired ? "bg-green-500/10 border border-green-500/30 text-green-700 dark:text-green-400" : "bg-red-500/10 border border-red-500/30 text-red-700 dark:text-red-400"}`}>
            {forceBriefResult.fired ? forceBriefResult.message : `❌ ${forceBriefResult.error}`}
          </div>
        )}

        {forceBriefResult?.sample_lines && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">sample_lines sent to worker:</p>
            <pre className="bg-muted rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
              {forceBriefResult.sample_lines.join("\n")}
            </pre>
          </div>
        )}
      </div>

      {/* Railway Raw Log Diagnostic */}
      <div className="border rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Railway Raw Log Diagnostic</h2>
            <p className="text-sm text-muted-foreground">
              Fetches live Railway logs and shows exactly what format they arrive in + what blocks the incident extractor builds.
            </p>
          </div>
          <button
            onClick={fetchRailwayRaw}
            disabled={railwayRawLoading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            {railwayRawLoading ? "Fetching…" : "Fetch Raw Logs"}
          </button>
        </div>

        {railwayRawError && (
          <div className="bg-destructive/10 border border-destructive/30 rounded p-3 text-sm text-destructive">
            {railwayRawError}
          </div>
        )}

        {railwayRaw && (
          <div className="space-y-3">
            {!!railwayRaw.error && (
              <div className="bg-red-500/10 border border-red-500/30 rounded p-2 text-xs text-red-700 dark:text-red-400">
                {String(railwayRaw.error)}
              </div>
            )}
            {!railwayRaw.error && (
              <>
                <table className="w-full text-sm">
                  <tbody className="divide-y">
                    <Row label="Service" value={`${String(railwayRaw.project ?? "")} / ${String(railwayRaw.service ?? "")}`} />
                    <Row label="Raw entries from Railway" value={String(railwayRaw.total_log_entries_from_railway ?? 0)} />
                    <Row label="Lines after splitlines()" value={String(railwayRaw.lines_after_splitlines ?? 0)} />
                    <Row label="Incident blocks found" value={String(railwayRaw.incident_blocks_found ?? 0)} />
                  </tbody>
                </table>

                {Array.isArray(railwayRaw.raw_entry_sample_last_20) && railwayRaw.raw_entry_sample_last_20.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">Last 20 raw Railway entries (severity + message format):</p>
                    <pre className="bg-muted rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                      {(railwayRaw.raw_entry_sample_last_20 as {severity: string; message_has_newline: boolean; message_preview: string}[]).map((e, i) =>
                        `[${e.severity}] has_newline=${e.message_has_newline} | ${e.message_preview}`
                      ).join("\n")}
                    </pre>
                  </div>
                )}

                {Array.isArray(railwayRaw.incident_blocks) && railwayRaw.incident_blocks.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">Incident blocks extracted:</p>
                    <div className="space-y-2">
                      {(railwayRaw.incident_blocks as {trigger_line: string; block_size: number; has_file_lines: boolean; has_traceback: boolean; block_lines: string[]}[]).map((b, i) => (
                        <div key={i} className={`rounded p-2 text-xs border ${b.has_file_lines ? "border-green-500/40 bg-green-500/5" : "border-red-500/30 bg-red-500/5"}`}>
                          <div className="font-medium mb-1">
                            Block {i + 1}: {b.block_size} line(s) | File lines: {b.has_file_lines ? "✅" : "❌"} | Traceback: {b.has_traceback ? "✅" : "❌"}
                          </div>
                          <pre className="whitespace-pre-wrap opacity-80">{b.block_lines.join("\n")}</pre>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Latest RRT Brief Inspector */}
      <div className="border rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Latest RRT Brief Inspector</h2>
            <p className="text-sm text-muted-foreground">
              Shows the 5 most recent briefs from the DB — checks which ones have traceback lines and code frames.
            </p>
          </div>
          <button
            onClick={fetchLatestBrief}
            disabled={briefLoading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            {briefLoading ? "Loading…" : "Inspect Briefs"}
          </button>
        </div>

        {briefError && (
          <div className="bg-destructive/10 border border-destructive/30 rounded p-3 text-sm text-destructive">
            {briefError}
          </div>
        )}

        {briefResult && !briefResult.found && (
          <div className="bg-yellow-500/10 border border-yellow-500/30 rounded p-3 text-sm text-yellow-700 dark:text-yellow-400">
            ⚠️ {briefResult.message}
          </div>
        )}

        {briefResult?.found && (
          <div className="space-y-4">
            <div className={`rounded p-3 text-sm font-medium ${briefResult.any_with_code_frames ? "bg-green-500/10 border border-green-500/30 text-green-700 dark:text-green-400" : briefResult.any_with_file_lines ? "bg-yellow-500/10 border border-yellow-500/30 text-yellow-700 dark:text-yellow-400" : "bg-red-500/10 border border-red-500/30 text-red-700 dark:text-red-400"}`}>
              {briefResult.any_with_code_frames ? "✅ At least one brief has code_frames — source code IS being fetched" : briefResult.any_with_file_lines ? "⚠️ File lines present in sample but no code_frames — worker fetch failing" : "❌ None of the last 5 briefs have File lines — block extractor still not capturing tracebacks"}
            </div>

            {Array.isArray(briefResult.briefs) && briefResult.briefs.map((b: Record<string, unknown>, i: number) => (
              <div key={String(b.brief_id)} className="border rounded p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-muted-foreground">{String(b.brief_id ?? "").slice(0, 8)}… — {String(b.created_at ?? "")}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${(b.code_frames_stored as number) > 0 ? "bg-green-500/20 text-green-700" : (b.error_sample_has_file_lines as boolean) ? "bg-yellow-500/20 text-yellow-700" : "bg-red-500/20 text-red-700"}`}>
                    {String(b.diagnosis ?? "")}
                  </span>
                </div>
                <table className="w-full text-xs">
                  <tbody className="divide-y">
                    <Row label="Sample lines" value={String(b.error_sample_line_count ?? 0)} />
                    <Row label="Has Traceback" value={(b.error_sample_has_traceback as boolean) ? "✅" : "❌"} />
                    <Row label='Has File "..."' value={(b.error_sample_has_file_lines as boolean) ? "✅" : "❌"} />
                    <Row label="Parseable frames" value={String(b.would_parse_frame_count ?? 0)} />
                    <Row label="code_frames stored" value={String(b.code_frames_stored ?? 0)} />
                  </tbody>
                </table>
                {Array.isArray(b.error_sample_first_10_lines) && (b.error_sample_first_10_lines as string[]).length > 0 && (
                  <pre className="bg-muted rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap">
                    {(b.error_sample_first_10_lines as string[]).join("\n")}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Code Context Pipeline Test */}
      <div className="border rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Code Context Pipeline Test</h2>
            <p className="text-sm text-muted-foreground">
              Runs the full traceback → frame extraction → GitHub/local fetch chain directly.
              Tells you exactly where it breaks.
            </p>
          </div>
          <button
            onClick={runCodeContextTest}
            disabled={ctxLoading}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            {ctxLoading ? "Testing…" : "Run Test"}
          </button>
        </div>

        {ctxError && (
          <div className="bg-destructive/10 border border-destructive/30 rounded p-3 text-sm text-destructive">
            {ctxError}
          </div>
        )}

        {ctxResult && (
          <div className="space-y-3">
            {!!ctxResult.error && (
              <div className="bg-destructive/10 border border-destructive/30 rounded p-3 text-sm text-destructive font-medium">
                ❌ {String(ctxResult.error)}
              </div>
            )}
            <table className="w-full text-sm">
              <tbody className="divide-y">
                <Row label="Frames parsed" value={Array.isArray(ctxResult.parsed_frames) ? String((ctxResult.parsed_frames as unknown[]).length) : "0"} />
                <Row label="Code frames fetched" value={String(ctxResult.code_frames_count ?? 0)} />
                <Row label="Local file exists" value={ctxResult.local_file_exists_at_app ? "✅ /app/apps/api/routers/dev_tools.py" : "❌ Not found"} />
                <Row label="Token decryptable" value={ctxResult.github_token_decryptable ? `✅ ${String(ctxResult.github_token_preview)}` : `❌ ${String(ctxResult.github_token_preview)}`} />
              </tbody>
            </table>
            {Array.isArray(ctxResult.code_frames) && (ctxResult.code_frames as unknown[]).length > 0 && (
              <div className="bg-green-500/10 border border-green-500/30 rounded p-3 text-sm text-green-700 dark:text-green-400">
                ✅ Code context works! Source: {String((ctxResult.code_frames as Record<string,unknown>[])[0]?.source)} — {String((ctxResult.code_frames as Record<string,unknown>[])[0]?.snippet_lines)} lines fetched
              </div>
            )}
            {Number(ctxResult.code_frames_count) === 0 && !ctxResult.error && (
              <div className="bg-yellow-500/10 border border-yellow-500/30 rounded p-3 text-sm text-yellow-700 dark:text-yellow-400">
                ⚠️ Frames were parsed but code fetch returned empty. Check Railway worker logs for the exact error — look for lines starting with "GitHub code context" or "Local code context".
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td className="py-2 pr-4 text-muted-foreground font-medium w-48">{label}</td>
      <td className="py-2 font-mono text-xs break-all">{value}</td>
    </tr>
  );
}
