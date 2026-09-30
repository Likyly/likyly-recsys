/**
 * Every error the SDK throws extends LikylyError, so `catch (e) { if (e instanceof LikylyError) ... }`
 * is enough to separate LIKYLY failures from your own bugs.
 *
 *   LikylyError
 *   ├─ NetworkError            no HTTP response (DNS, connection reset, ...)
 *   ├─ TimeoutError            the request timed out
 *   ├─ ValidationError         invalid request - caught by the SDK before sending, or 422 from the API
 *   └─ ApiError                the API answered with an error status
 *      ├─ AuthenticationError  401 - missing / invalid / revoked API key
 *      ├─ PermissionDeniedError 403 - e.g. a public key used for a secret-key operation, or a plan limit
 *      ├─ NotFoundError        404
 *      └─ RateLimitError       429 - see `retryAfter`
 */
export interface ErrorDetails {
  statusCode?: number;
  requestId?: string;
  retryAfter?: number;
  body?: unknown;
  cause?: unknown;
}

export class LikylyError extends Error {
  /** HTTP status code, when there was an HTTP response. */
  readonly statusCode?: number;
  /** The `request_id` of the failed call (also the `X-Request-ID` response header) - quote it when reporting a problem. */
  readonly requestId?: string;
  /** Seconds to wait before retrying, from the `Retry-After` header (429/503). */
  readonly retryAfter?: number;
  /** The parsed response body, when there was one. */
  readonly body?: unknown;

  constructor(message: string, details: ErrorDetails = {}) {
    super(message, details.cause !== undefined ? { cause: details.cause } : undefined);
    this.name = new.target.name;
    this.statusCode = details.statusCode;
    this.requestId = details.requestId;
    this.retryAfter = details.retryAfter;
    this.body = details.body;
  }
}

export class NetworkError extends LikylyError {}
export class TimeoutError extends LikylyError {}
export class ValidationError extends LikylyError {}
export class ApiError extends LikylyError {}
export class AuthenticationError extends ApiError {}
export class PermissionDeniedError extends ApiError {}
export class NotFoundError extends ApiError {}
export class RateLimitError extends ApiError {}

export function errorFromResponse(status: number, body: unknown, requestId: string | undefined, retryAfter: number | undefined): ApiError | ValidationError {
  const message = extractMessage(body) ?? `LIKYLY API error (HTTP ${status})`;
  const details = { statusCode: status, requestId, retryAfter, body };
  switch (status) {
    case 401:
      return new AuthenticationError(message, details);
    case 403:
      return new PermissionDeniedError(message, details);
    case 404:
      return new NotFoundError(message, details);
    case 422:
      return new ValidationError(message, details);
    case 429:
      return new RateLimitError(message, details);
    default:
      return new ApiError(message, details);
  }
}

function extractMessage(body: unknown): string | undefined {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
        .join("; ");
    }
    return JSON.stringify(detail);
  }
  return undefined;
}
