<?php

declare(strict_types=1);

namespace Likyly\Model;

final class Item
{
    /**
     * @param array<string, mixed> $properties free-form: category, price, brand, ... (keys are yours)
     * @param string $itemId your own identifier - any string (`SKU-123`, a UUID, `gid://shopify/Product/123`)
     */
    public function __construct(
        public readonly string $itemId,
        public readonly string $title,
        public readonly ?string $description = null,
        public readonly array $properties = [],
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self((string) $w['item_id'], (string) $w['title'], $w['description'] ?? null, $w['properties'] ?? []);
    }
}
