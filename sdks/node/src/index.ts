/**
 * OpsLens AI Node.js SDK
 * ======================
 * Lightweight TypeScript/JavaScript client for pushing log events
 * into the OpsLens AI alert pipeline.
 *
 * @example
 * ```ts
 * import { OpsLens } from 'opslens';
 *
 * const client = new OpsLens({ apiKey: 'opsl_...' });
 *
 * await client.ingest({
 *   service: 'payment-service',
 *   error: 'PaymentError: Stripe timeout after 30s',
 *   severity: 'p1',
 *   count: 47,
 * });
 * ```
 */

const DEFAULT_BASE_URL = 'https://opslensai.com';
const SDK_VERSION = '0.1.0';

// ── Types ──────────────────────────────────────────────────────────────────────

export type Severity = 'p0' | 'p1' | 'p2' | 'p3';
export type LogLevel = 'ERROR' | 'CRITICAL' | 'FATAL' | 'WARN' | 'INFO';

export interface OpsLensOptions {
  /**
   * Your OpsLens API key (starts with `opsl_`).
   * Falls back to the `OPSLENS_API_KEY` environment variable.
   */
  apiKey?: string;
  /**
   * Override the base URL for self-hosted or staging deployments.
   * @default "https://opslensai.com"
   */
  baseUrl?: string;
  /**
   * Request timeout in milliseconds.
   * @default 10000
   */
  timeoutMs?: number;
}

export interface IngestOptions {
  /** Name of the service or container, e.g. `"payment-service"`. */
  service: string;
  /** The error or exception text. */
  error: string;
  /**
   * Priority level: `"p0"` (critical) through `"p3"` (low).
   * @default "p2"
   */
  severity?: Severity;
  /**
   * Number of occurrences in the current window.
   * @default 1
   */
  count?: number;
  /**
   * Log level string.
   * @default "ERROR"
   */
  logLevel?: LogLevel;
  /**
   * ISO 8601 timestamp of the first occurrence. Defaults to now.
   */
  timestamp?: Date | string;
  /**
   * Arbitrary key/value pairs forwarded to the incident brief.
   */
  metadata?: Record<string, unknown>;
}

export interface IngestResult {
  status: string;
  message: string;
  taskId: string | null;
  errorSignature: string;
}

// ── Errors ─────────────────────────────────────────────────────────────────────

export class OpsLensError extends Error {
  readonly statusCode: number | undefined;

  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = 'OpsLensError';
    this.statusCode = statusCode;
  }
}

export class AuthenticationError extends OpsLensError {
  constructor(message: string, statusCode?: number) {
    super(message, statusCode);
    this.name = 'AuthenticationError';
  }
}

export class RateLimitError extends OpsLensError {
  constructor(message = 'Rate limit exceeded. Slow down or upgrade your plan.') {
    super(message, 429);
    this.name = 'RateLimitError';
  }
}

// ── Client ─────────────────────────────────────────────────────────────────────

export class OpsLens {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;

  constructor(options: OpsLensOptions = {}) {
    const key = options.apiKey ?? process.env['OPSLENS_API_KEY'] ?? '';
    if (!key) {
      throw new AuthenticationError(
        'No API key provided. Pass apiKey or set OPSLENS_API_KEY.'
      );
    }
    this.apiKey = key;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, '');
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  /**
   * Push a log error event into the OpsLens AI alert pipeline.
   *
   * The event is immediately routed through your configured routing rules
   * and triggers an AI-generated incident brief if thresholds are met.
   *
   * @example
   * ```ts
   * const result = await client.ingest({
   *   service: 'checkout-service',
   *   error: 'CardDeclinedError: card_id=card_abc123',
   *   severity: 'p2',
   *   count: 3,
   *   metadata: { userId: 'usr_789', region: 'us-east-1' },
   * });
   * console.log(result.status); // "queued"
   * ```
   */
  async ingest(opts: IngestOptions): Promise<IngestResult> {
    const payload = this.buildPayload(opts);
    const url = `${this.baseUrl}/api/v1/log-ops/ingest`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        throw new OpsLensError(`Request timed out after ${this.timeoutMs}ms`);
      }
      throw new OpsLensError(`Network error: ${String(err)}`);
    } finally {
      clearTimeout(timer);
    }

    return this.handleResponse(response);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────

  private headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      'Content-Type': 'application/json',
      'User-Agent': `opslens-node/${SDK_VERSION}`,
    };
  }

  private buildPayload(opts: IngestOptions): Record<string, unknown> {
    let ts: string | undefined;
    if (opts.timestamp instanceof Date) {
      ts = opts.timestamp.toISOString();
    } else if (typeof opts.timestamp === 'string') {
      ts = opts.timestamp;
    }

    const payload: Record<string, unknown> = {
      service_name: opts.service,
      error_message: opts.error,
      severity: opts.severity ?? 'p2',
      error_count: opts.count ?? 1,
      log_level: opts.logLevel ?? 'ERROR',
    };

    if (ts) payload['timestamp'] = ts;
    if (opts.metadata) payload['metadata'] = opts.metadata;

    return payload;
  }

  private async handleResponse(resp: Response): Promise<IngestResult> {
    if (resp.status === 401 || resp.status === 403) {
      throw new AuthenticationError('Invalid or missing API key.', resp.status);
    }
    if (resp.status === 429) {
      throw new RateLimitError();
    }
    if (resp.status < 200 || resp.status >= 300) {
      let detail = resp.statusText;
      try {
        const body = await resp.json() as { detail?: string };
        detail = body.detail ?? detail;
      } catch { /* ignore parse errors */ }
      throw new OpsLensError(`API error ${resp.status}: ${detail}`, resp.status);
    }

    const data = await resp.json() as {
      status: string;
      message: string;
      task_id?: string | null;
      error_signature: string;
    };

    return {
      status: data.status,
      message: data.message,
      taskId: data.task_id ?? null,
      errorSignature: data.error_signature,
    };
  }
}

export default OpsLens;
