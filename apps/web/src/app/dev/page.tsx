"use client";

import { useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { demoApi } from "@/lib/api";

export default function DevDiagnosticsPage() {
  const { getToken } = useAuth();
  const [githubResult, setGithubResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [ctxResult, setCtxResult] = useState<Record<string, unknown> | null>(null);
  const [ctxLoading, setCtxLoading] = useState(false);
  const [ctxError, setCtxError] = useState<string | null>(null);

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
