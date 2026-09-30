<?php

declare(strict_types=1);

namespace Likyly\Model;

final class ItemList
{
    /**
     * @param Item[] $items
     * @param int|null $total total number of items in the catalog (X-Total-Count), when the API reported it
     * @param int|null $limit the page size you asked for (null = the API's default)
     */
    public function __construct(
        public readonly array $items,
        public readonly ?int $total = null,
        public readonly ?int $limit = null,
        public readonly int $offset = 0,
    ) {
    }
}
