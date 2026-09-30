<?php

declare(strict_types=1);

namespace Likyly\Http;

use GuzzleHttp\ClientInterface;
use GuzzleHttp\Exception\ConnectException;
use GuzzleHttp\Exception\GuzzleException;
use GuzzleHttp\Exception\RequestException;
use Likyly\Exception\LikylyException;
use Likyly\Exception\NetworkException;
use Likyly\Exception\TimeoutException;

/**
 * The only place that talks HTTP. Every resource goes through request(): auth header, catalog
 * parameter, timeout, retries with exponential backoff + jitter, and the error mapping.
 *
 * @internal
 */
final class HttpClient
{
    private const RETRY_BASE_S = 0.5;
    private const RETRY_CAP_S = 8.0;
    /** A Retry-After longer than this is not waited for: the RateLimitException is thrown instead. */
    private const MAX_RETRY_AFTER_S = 60.0;

    /** @var callable(float): void */
    private $sleep;
    /** @var callable(): float */
    private $random;

    /**
     * @param callable(float): void|null $sleep test hook
     * @param callable(): float|null $random test hook
     */
    public function __construct(
        private readonly ClientInterface $http,
        private readonly string $apiKey,
        private readonly string $baseUrl,
        private readonly ?string $catalog,
        private readonly float $timeout,
        private readonly int $maxRetries,
        private readonly string $userAgent,
        ?callable $sleep = null,
        ?callable $random = null,
    ) {
        $this->sleep = $sleep ?? static function (float $seconds): void {
            usleep((int) round($seconds * 1_000_000));
        };
        $this->random = $random ?? static fn (): float => mt_rand() / mt_getrandmax();
    }

    /**
     * @param array<string, scalar|null> $query
     * @param array<string, mixed>|\stdClass|null $body a \stdClass is sent as a JSON object (an empty array would become `[]`)
     * @param bool $idempotent safe to send again if the outcome is unknown (a timeout, a 5xx, a dropped
     *                         connection)? false for events without an eventId, where a blind retry could
     *                         record the event twice
     * @param array{timeout?: float, maxRetries?: int} $options per-call overrides
     */
    public function request(string $method, string $path, array $query = [], array|\stdClass|null $body = null, bool $idempotent = true, array $options = []): Response
    {
        $maxRetries = $options['maxRetries'] ?? $this->maxRetries;
        $timeout = $options['timeout'] ?? $this->timeout;
        $attempt = 0;
        while (true) {
            try {
                return $this->once($method, $path, $query, $body, $timeout);
            } catch (LikylyException $error) {
                $delay = $this->retryDelay($error, $attempt, $idempotent);
                if ($delay === null || $attempt >= $maxRetries) {
                    throw $error;
                }
                ++$attempt;
                ($this->sleep)($delay);
            }
        }
    }

    private function retryDelay(LikylyException $error, int $attempt, bool $idempotent): ?float
    {
        $backoff = ($this->random)() * min(self::RETRY_CAP_S, self::RETRY_BASE_S * (2 ** $attempt)); // full jitter
        if ($error->statusCode === 429) {
            // Rejected by the rate limiter before reaching the application: nothing was processed, so
            // retrying is safe for every request.
            if ($error->retryAfter !== null) {
                return $error->retryAfter <= self::MAX_RETRY_AFTER_S ? $error->retryAfter : null;
            }

            return $backoff;
        }
        if (!$idempotent) {
            return null; // the outcome is unknown - never risk a duplicate
        }
        if (in_array($error->statusCode, [502, 503, 504], true)) {
            if ($error->retryAfter !== null) {
                return $error->retryAfter <= self::MAX_RETRY_AFTER_S ? $error->retryAfter : null;
            }

            return $backoff;
        }
        if ($error instanceof NetworkException || $error instanceof TimeoutException) {
            return $backoff;
        }

        return null;
    }

    /**
     * @param array<string, scalar|null> $query
     * @param array<string, mixed>|\stdClass|null $body
     */
    private function once(string $method, string $path, array $query, array|\stdClass|null $body, float $timeout): Response
    {
        $params = array_filter($query, static fn ($v) => $v !== null);
        if ($this->catalog !== null && $this->catalog !== '') {
            $params['data_product_type'] = $this->catalog;
        }
        $url = $this->baseUrl . $path . ($params === [] ? '' : '?' . http_build_query($params, '', '&', PHP_QUERY_RFC3986));

        $options = [
            'headers' => ['X-API-Key' => $this->apiKey, 'Accept' => 'application/json', 'User-Agent' => $this->userAgent],
            'http_errors' => false,
            'timeout' => $timeout,
            'connect_timeout' => $timeout,
        ];
        if ($body !== null) {
            $options['json'] = $body;
        }

        try {
            $response = $this->http->request($method, $url, $options);
        } catch (ConnectException | RequestException $e) {
            if ($this->isTimeout($e)) {
                throw new TimeoutException("LIKYLY request timed out after {$timeout} s", previous: $e);
            }
            throw new NetworkException('Could not reach the LIKYLY API: ' . $e->getMessage(), previous: $e);
        } catch (GuzzleException $e) {
            throw new NetworkException('Could not reach the LIKYLY API: ' . $e->getMessage(), previous: $e);
        }

        $text = (string) $response->getBody();
        $data = null;
        if ($text !== '') {
            $decoded = json_decode($text, true);
            $data = json_last_error() === JSON_ERROR_NONE ? $decoded : $text; // e.g. an HTML 502 page from a proxy
        }
        $headers = [];
        foreach ($response->getHeaders() as $name => $values) {
            $headers[strtolower((string) $name)] = implode(', ', $values);
        }

        if ($response->getStatusCode() >= 400) {
            $requestId = $headers['x-request-id'] ?? (is_array($data) && isset($data['request_id']) ? (string) $data['request_id'] : null);
            throw LikylyException::fromResponse($response->getStatusCode(), $data, $requestId, self::parseRetryAfter($headers['retry-after'] ?? null));
        }

        return new Response($response->getStatusCode(), $headers, $data);
    }

    private function isTimeout(\Throwable $e): bool
    {
        $context = method_exists($e, 'getHandlerContext') ? $e->getHandlerContext() : [];

        return ($context['errno'] ?? null) === 28 || stripos($e->getMessage(), 'timed out') !== false;
    }

    private static function parseRetryAfter(?string $value): ?float
    {
        if ($value === null || $value === '') {
            return null;
        }
        if (is_numeric($value)) {
            return max(0.0, (float) $value);
        }
        $date = strtotime($value);

        return $date === false ? null : max(0.0, (float) ($date - time()));
    }
}
