<?php

declare(strict_types=1);

namespace Likyly\Model;

final class RecommendationResponse
{
    /**
     * @param string $recommendationId send it back on the impression / click / add_to_cart / purchase events for these items
     * @param string $strategy what LIKYLY used: hybrid, content, collaborative, session or popular
     * @param RecommendedItem[] $items
     */
    public function __construct(
        public readonly string $recommendationId,
        public readonly string $strategy,
        public readonly array $items,
        public readonly ?string $placement = null,
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self(
            (string) $w['recommendation_id'],
            (string) $w['strategy'],
            array_map(static fn (array $i) => RecommendedItem::fromWire($i), $w['items']),
            $w['placement'] ?? null,
        );
    }
}
