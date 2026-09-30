<?php

declare(strict_types=1);

namespace Likyly\Exception;

/**
 * Every exception the SDK throws extends LikylyException:
 *
 *   LikylyException
 *   ├─ NetworkException            no HTTP response (DNS, connection reset, ...)
 *   ├─ TimeoutException            the request timed out
 *   ├─ ValidationException         invalid request - caught by the SDK before sending, or 422 from the API
 *   └─ ApiException                the API answered with an error status
 *      ├─ AuthenticationException  401 - missing / invalid / revoked API key
 *      ├─ PermissionDeniedException 403 - e.g. a public key used for a secret-key operation, or a plan limit
 *      ├─ NotFoundException        404
 *      └─ RateLimitException       429 - see $retryAfter
 */
class LikylyException extends \RuntimeException
{
    /**
     * @param mixed $body the parsed response body, when there was one
     */
    public function __construct(
        string $message,
        public readonly ?int $statusCode = null,
        /** The request_id of the failed call (also the X-Request-ID header) - quote it when reporting a problem. */
        public readonly ?string $requestId = null,
        /** Seconds to wait before retrying, from the Retry-After header (429/503). */
        public readonly ?float $retryAfter = null,
        public readonly mixed $body = null,
        ?\Throwable $previous = null,
    ) {
        parent::__construct($message, 0, $previous);
    }

    /**
     * @param mixed $body
     */
    public static function fromResponse(int $status, mixed $body, ?string $requestId, ?float $retryAfter): self
    {
        $message = self::messageOf($body) ?? "LIKYLY API error (HTTP {$status})";
        $class = match ($status) {
            401 => AuthenticationException::class,
            403 => PermissionDeniedException::class,
            404 => NotFoundException::class,
            422 => ValidationException::class,
            429 => RateLimitException::class,
            default => ApiException::class,
        };

        return new $class($message, $status, $requestId, $retryAfter, $body);
    }

    private static function messageOf(mixed $body): ?string
    {
        if (!is_array($body) || !array_key_exists('detail', $body)) {
            return null;
        }
        $detail = $body['detail'];
        if (is_string($detail)) {
            return $detail;
        }
        if (is_array($detail)) {
            return implode('; ', array_map(
                static fn ($d) => is_array($d) && isset($d['msg']) ? (string) $d['msg'] : json_encode($d),
                $detail,
            ));
        }

        return json_encode($detail) ?: null;
    }
}
