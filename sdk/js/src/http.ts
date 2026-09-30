import {
  LikylyError,
  NetworkError,
  TimeoutError,
  ValidationError,
  errorFromResponse,
} from "./errors.js";
import type { RequestOptions } from "./types.js";

export interface HttpConfig {
  apiKey: string;
  baseUrl: string;
  catalog?: string;
  timeout: number;
  maxRetries: number;
  userAgent: string;
  fetch: typeof fetch;
  /** Test hooks - not part of the public API. */
  sleep: (ms: number) => Promise<void>;
  random: () => number;
}

export interface HttpRequest {
  method: "GET" | "POST" | "PUT" | "DELETE";
  /** Path already percent-encoded, starting with "/". */
  path: string;
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  /**
   * Safe to send again if the outcome is unknown (a timeout, a 5xx, a dropped connection)? True for
   * reads, PUT/DELETE and the upserts; false for events without an `eventId`, where a blind retry
   * could record the event twice.
   */
  idempotent: boolean;
  options?: RequestOptions;
}

export interface HttpResponse<T> {
  data: T;
  status: number;
  headers: Headers;
  requestId?: string;
}

const RETRY_BASE_MS = 500;
const RETRY_CAP_MS = 8_000;
/** A Retry-After longer than this is not waited for: the RateLimitError is thrown instead. */
const MAX_RETRY_AFTER_MS = 60_000;

/**
 * The only place that talks HTTP. Every resource goes through `request()`: auth header, catalog
 * parameter, timeout, retries with exponential backoff + jitter, and the error mapping.
 */
export class HttpClient {
  constructor(private readonly config: HttpConfig) {}

  async request<T>(req: HttpRequest): Promise<HttpResponse<T>> {
    const maxRetries = req.options?.maxRetries ?? this.config.maxRetries;
    const url = this.buildUrl(req);
    let attempt = 0;

    for (;;) {
      let failure: LikylyError;
      try {
        return await this.attempt<T>(req, url);
      } catch (error) {
        if (!(error instanceof LikylyError)) throw error; // e.g. the caller's own AbortError
        failure = error;
      }

      const delay = this.retryDelay(failure, attempt, req.idempotent);
      if (delay === undefined || attempt >= maxRetries) throw failure;
      attempt += 1;
      await this.config.sleep(delay);
    }
  }

  /** Milliseconds to wait before the next attempt, or undefined if this failure must not be retried. */
  private retryDelay(error: LikylyError, attempt: number, idempotent: boolean): number | undefined {
    const backoff = () => this.config.random() * Math.min(RETRY_CAP_MS, RETRY_BASE_MS * 2 ** attempt); // full jitter
    if (error.statusCode === 429) {
      // Rejected by the rate limiter before reaching the application: nothing was processed, so
      // retrying is safe for every request.
      if (error.retryAfter !== undefined) {
        const wait = error.retryAfter * 1000;
        return wait <= MAX_RETRY_AFTER_MS ? wait : undefined;
      }
      return backoff();
    }
    if (!idempotent) return undefined; // the outcome is unknown - never risk a duplicate
    if (error.statusCode === 502 || error.statusCode === 503 || error.statusCode === 504) {
      if (error.retryAfter !== undefined) {
        const wait = error.retryAfter * 1000;
        return wait <= MAX_RETRY_AFTER_MS ? wait : undefined;
      }
      return backoff();
    }
    if (error instanceof NetworkError || error instanceof TimeoutError) return backoff();
    return undefined;
  }

  private buildUrl(req: HttpRequest): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(req.query ?? {})) {
      if (value !== undefined) params.set(key, String(value));
    }
    if (this.config.catalog) params.set("data_product_type", this.config.catalog);
    const qs = params.toString();
    return this.config.baseUrl + req.path + (qs ? `?${qs}` : "");
  }

  private async attempt<T>(req: HttpRequest, url: string): Promise<HttpResponse<T>> {
    const timeoutMs = req.options?.timeout ?? this.config.timeout;
    const controller = new AbortController();
    const userSignal = req.options?.signal;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onUserAbort = () => controller.abort();
    if (userSignal) {
      if (userSignal.aborted) controller.abort();
      else userSignal.addEventListener("abort", onUserAbort, { once: true });
    }

    const headers: Record<string, string> = {
      "X-API-Key": this.config.apiKey,
      Accept: "application/json",
      "User-Agent": this.config.userAgent,
    };
    if (req.body !== undefined) headers["Content-Type"] = "application/json";

    let response: Response;
    try {
      response = await this.config.fetch(url, {
        method: req.method,
        headers,
        body: req.body !== undefined ? JSON.stringify(req.body) : undefined,
        signal: controller.signal,
      });
    } catch (cause) {
      if (timedOut) throw new TimeoutError(`LIKYLY request timed out after ${timeoutMs} ms`, { cause });
      if (userSignal?.aborted) throw cause; // the caller cancelled: surface their AbortError untouched
      throw new NetworkError(`Could not reach the LIKYLY API: ${(cause as Error)?.message ?? cause}`, { cause });
    } finally {
      clearTimeout(timer);
      userSignal?.removeEventListener("abort", onUserAbort);
    }

    const text = await response.text().catch(() => "");
    let data: unknown = undefined;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text; // e.g. an HTML 502 page from a proxy
      }
    }
    const requestId =
      response.headers.get("x-request-id") ??
      (data && typeof data === "object" && "request_id" in data ? String((data as { request_id: unknown }).request_id) : undefined);

    if (!response.ok) {
      throw errorFromResponse(response.status, data, requestId ?? undefined, parseRetryAfter(response.headers.get("retry-after")));
    }
    return { data: data as T, status: response.status, headers: response.headers, requestId: requestId ?? undefined };
  }
}

/** Retry-After is either a number of seconds or an HTTP date. */
export function parseRetryAfter(value: string | null): number | undefined {
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds;
  const date = Date.parse(value);
  if (!Number.isNaN(date)) return Math.max(0, Math.ceil((date - Date.now()) / 1000));
  return undefined;
}

export function assertNonEmptyObject(value: unknown, name: string): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ValidationError(`${name} must be an object`);
  }
}
