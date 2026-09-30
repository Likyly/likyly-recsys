import { ValidationError } from "./errors.js";

/**
 * Percent-encodes a path segment: everything outside RFC 3986 "unreserved" (A-Z a-z 0-9 - . _ ~)
 * becomes %XX (UTF-8), '/' included - so an id like "gid://shopify/Product/1" is one segment.
 */
export function encodeSegment(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

/** Ids are opaque, non-empty strings - never coerced to numbers. */
export function requireId(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ValidationError(`${name} must be a non-empty string (your own identifier, e.g. "SKU-123")`);
  }
  return value;
}

/** Advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used. */
export function requirePathSafeId(value: unknown, name: string): string {
  const id = requireId(value, name);
  if (id.includes("/")) {
    throw new ValidationError(
      `${name} "${id}" contains "/", which this endpoint cannot carry in its URL path - use recommendations.get(), which takes ids in the request body`,
    );
  }
  return id;
}

/** Removes undefined values so optional fields are simply not sent. */
export function compact<T extends Record<string, unknown>>(obj: T): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) if (value !== undefined) out[key] = value;
  return out;
}
