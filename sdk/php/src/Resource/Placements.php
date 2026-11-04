<?php

declare(strict_types=1);

namespace Likyly\Resource;

use Likyly\Exception\ValidationException;
use Likyly\Http\HttpClient;
use Likyly\Internal\Support;
use Likyly\Model\PlacementRecommendation;

/**
 * `$likyly->placements()`. The recommended way to show recommendations: a placement (`pdp-related`,
 * `home-for-you`...) is configured once in LIKYLY - strategy, fallback, filters - and called by its slug.
 * Works with the public key.
 */
final class Placements
{
    /** Context keys as this SDK names them => as the API names them. */
    private const CONTEXT = [
        'itemId' => 'current_item_id',
        'categoryId' => 'category_id',
        'collectionId' => 'collection_id',
        'userId' => 'user_id',
        'anonymousId' => 'anonymous_id',
        'sessionId' => 'session_id',
        'cartItemIds' => 'cart_item_ids',
        'locale' => 'locale',
        'customContext' => 'custom_context',
    ];

    public function __construct(private readonly HttpClient $http)
    {
    }

    /**
     * Recommendations for one placement. `$context` is what you know about the moment: `itemId` (the item
     * being viewed), `userId`, `sessionId`, `anonymousId`, `categoryId`, `collectionId`, `cartItemIds`,
     * `locale`, `customContext` - the placement decides what it uses. Send `recommendationId` back on the
     * events that follow.
     *
     * @param array<string, mixed>|null $context
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function recommend(string $placement, ?array $context = null, ?int $limit = null, array $options = []): PlacementRecommendation
    {
        $wire = [];
        foreach ($context ?? [] as $key => $value) {
            if (!isset(self::CONTEXT[$key])) {
                throw new ValidationException("unknown placement context key: {$key}");
            }
            if ($value !== null) {
                $wire[self::CONTEXT[$key]] = $value;
            }
        }
        $body = Support::compact(['context' => $wire === [] ? null : $wire, 'limit' => $limit]);
        $path = '/placements/' . Support::encodeSegment(Support::requireId($placement, 'placement')) . '/recommend';
        $r = $this->http->request('POST', $path, body: $body === [] ? new \stdClass() : $body, options: $options);

        return PlacementRecommendation::fromWire($r->data);
    }
}
