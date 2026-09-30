<?php

declare(strict_types=1);

namespace Likyly\Resource;

use Likyly\Exception\ValidationException;
use Likyly\Http\HttpClient;
use Likyly\Internal\Support;
use Likyly\Model\RecommendationResponse;

/**
 * `$likyly->recommendations()`. Use get(): send what you know and LIKYLY picks the best strategy.
 * The other methods are the *Advanced Recommendations* - one strategy at a time.
 */
final class Recommendations
{
    public function __construct(private readonly HttpClient $http)
    {
    }

    /**
     * Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing
     * at all (then you get what is popular). `strategy` in the response says what LIKYLY used; send
     * `recommendationId` back on the events that follow. `limit` is 1-100 (default 10).
     *
     * @param string[]|null $viewedItemIds recently viewed items, oldest first
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function get(
        ?string $userId = null,
        ?string $sessionId = null,
        ?string $itemId = null,
        ?array $viewedItemIds = null,
        ?string $placement = null,
        ?int $limit = null,
        bool $debug = false,
        array $options = [],
    ): RecommendationResponse {
        $body = Support::compact([
            'user_id' => $userId !== null ? Support::requireId($userId, 'userId') : null,
            'session_id' => $sessionId !== null ? Support::requireId($sessionId, 'sessionId') : null,
            'item_id' => $itemId !== null ? Support::requireId($itemId, 'itemId') : null,
            'viewed_item_ids' => $viewedItemIds !== null ? array_map(static fn ($id): string => Support::requireId($id, 'viewedItemIds[]'), array_values($viewedItemIds)) : null,
            'placement' => $placement,
            'count' => $limit,
            'debug' => $debug ? true : null,
        ]);
        $r = $this->http->request('POST', '/getRec', body: $body === [] ? new \stdClass() : $body, options: $options);

        return RecommendationResponse::fromWire($r->data);
    }

    // ---- Advanced Recommendations: ids travel in the URL path here, so they cannot contain "/" ----

    /**
     * The most popular items - the fallback for a visitor with no history at all.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function popular(?int $limit = null, ?string $placement = null, ?string $sessionId = null, array $options = []): RecommendationResponse
    {
        return $this->advanced('/getRec/popular/' . Support::limit($limit), $placement, $sessionId, [], $options);
    }

    /**
     * Items similar to one item (content similarity). No user needed.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function similar(string $itemId, ?int $limit = null, ?string $placement = null, ?string $sessionId = null, array $options = []): RecommendationResponse
    {
        return $this->advanced('/getRec/content/' . Support::encodeSegment(Support::requirePathSafeId($itemId, 'itemId')) . '/' . Support::limit($limit), $placement, $sessionId, [], $options);
    }

    /**
     * What users with similar histories liked. Needs a trained model.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function collaborative(string $userId, ?int $limit = null, ?string $placement = null, ?string $sessionId = null, array $options = []): RecommendationResponse
    {
        return $this->advanced('/getRec/collaborative/' . Support::encodeSegment(Support::requirePathSafeId($userId, 'userId')) . '/' . Support::limit($limit), $placement, $sessionId, [], $options);
    }

    /**
     * Similar items, personalized for a user. `alpha` (0-1) weighs the collaborative signal against content similarity.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function hybrid(string $userId, string $itemId, ?int $limit = null, ?float $alpha = null, ?string $placement = null, ?string $sessionId = null, array $options = []): RecommendationResponse
    {
        $path = '/getRec/hybrid/' . Support::encodeSegment(Support::requirePathSafeId($userId, 'userId')) . '/' . Support::encodeSegment(Support::requirePathSafeId($itemId, 'itemId')) . '/' . Support::limit($limit);

        return $this->advanced($path, $placement, $sessionId, ['alpha' => $alpha], $options);
    }

    /**
     * Recency-weighted recommendations from what was viewed: pass `viewedItemIds` (an explicit list,
     * oldest first) **or** `userId` (LIKYLY's own history of that user's views) - exactly one.
     *
     * @param string[]|null $viewedItemIds
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function session(?array $viewedItemIds = null, ?string $userId = null, ?int $limit = null, ?string $placement = null, ?string $sessionId = null, array $options = []): RecommendationResponse
    {
        if (($viewedItemIds !== null) === ($userId !== null)) {
            throw new ValidationException('session() needs either viewedItemIds or userId (exactly one)');
        }
        if ($userId !== null) {
            return $this->advanced('/getRec/sessionForUser/' . Support::encodeSegment(Support::requirePathSafeId($userId, 'userId')) . '/' . Support::limit($limit), $placement, $sessionId, [], $options);
        }
        $ids = array_map(static fn ($id): string => Support::requireId($id, 'viewedItemIds[]'), array_values($viewedItemIds ?? []));
        if ($ids === []) {
            throw new ValidationException('viewedItemIds must contain at least one item id');
        }
        foreach ($ids as $id) {
            if (str_contains($id, ',')) {
                throw new ValidationException('an item id containing "," cannot be sent in this endpoint\'s comma-separated list - use recommendations()->get()');
            }
        }

        return $this->advanced('/getRec/session', $placement, $sessionId, ['viewed_item_ids' => implode(',', $ids), 'count' => Support::limit($limit)], $options);
    }

    /**
     * @param array<string, scalar|null> $extra
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    private function advanced(string $path, ?string $placement, ?string $sessionId, array $extra, array $options): RecommendationResponse
    {
        // response_format=object: always the same {recommendation_id, strategy, items} envelope
        $query = ['response_format' => 'object', 'placement' => $placement, 'session_id' => $sessionId] + $extra;

        return RecommendationResponse::fromWire($this->http->request('GET', $path, $query, options: $options)->data);
    }
}
