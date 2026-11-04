<?php

declare(strict_types=1);

namespace Likyly\Model;

/** What a placement returned: the items, the strategy LIKYLY ran, and the id to attribute events with. */
final class PlacementRecommendation
{
    /**
     * @param string $recommendationId send it back on the impression / click / add_to_cart / purchase events for these items
     * @param string $strategyUsed what LIKYLY used after any fallback, e.g. hybrid, content, popular
     * @param RecommendedItem[] $items
     */
    public function __construct(
        public readonly string $recommendationId,
        public readonly string $placement,
        public readonly string $strategyUsed,
        public readonly array $items,
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self(
            (string) $w['recommendation_id'],
            (string) $w['placement'],
            (string) $w['strategy_used'],
            array_map(static fn (array $i) => RecommendedItem::fromWire($i), $w['items']),
        );
    }
}
