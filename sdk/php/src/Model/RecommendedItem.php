<?php

declare(strict_types=1);

namespace Likyly\Model;

final class RecommendedItem
{
    /** @param array<string, mixed> $properties */
    public function __construct(
        public readonly string $itemId,
        public readonly ?float $score = null,
        public readonly ?string $title = null,
        public readonly ?string $description = null,
        public readonly array $properties = [],
        public readonly ?Explanation $explanation = null,
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self(
            (string) $w['item_id'],
            $w['score'] ?? null,
            $w['title'] ?? null,
            $w['description'] ?? null,
            $w['properties'] ?? [],
            isset($w['explanation']) ? Explanation::fromWire($w['explanation']) : null,
        );
    }
}
