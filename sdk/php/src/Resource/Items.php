<?php

declare(strict_types=1);

namespace Likyly\Resource;

use Likyly\Exception\ValidationException;
use Likyly\Http\HttpClient;
use Likyly\Internal\Support;
use Likyly\Model\BatchResult;
use Likyly\Model\Item;
use Likyly\Model\ItemList;

/**
 * `$likyly->items()` - your catalog. Needs the **secret** API key.
 *
 * Every method accepts an optional trailing `$options` array: `['timeout' => 3.0, 'maxRetries' => 0]`.
 */
final class Items
{
    public function __construct(private readonly HttpClient $http)
    {
    }

    /**
     * One item by your own id.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function get(string $itemId, array $options = []): Item
    {
        $r = $this->http->request('GET', '/items/' . Support::encodeSegment(Support::requireId($itemId, 'itemId')), options: $options);

        return Item::fromWire($r->data);
    }

    /**
     * One page of the catalog. Pagination is `limit` + `offset`; `total` is the whole catalog's size.
     *
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function list(?int $limit = null, ?int $offset = null, array $options = []): ItemList
    {
        $r = $this->http->request('GET', '/items', ['limit' => $limit, 'offset' => $offset], options: $options);
        $total = $r->headers['x-total-count'] ?? null;

        return new ItemList(
            array_map(static fn (array $w) => Item::fromWire($w), $r->data),
            $total === null ? null : (int) $total,
            $limit,
            $offset ?? 0,
        );
    }

    /**
     * Creates the item, or replaces it if it exists - idempotent. Fields you leave out are cleared.
     *
     * @param array<string, mixed>|null $properties category, brand, price, ... (keys are yours)
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function upsert(string $itemId, string $title, ?string $description = null, ?array $properties = null, array $options = []): Item
    {
        $r = $this->http->request('PUT', '/items/' . Support::encodeSegment(Support::requireId($itemId, 'itemId')), body: self::body($title, $description, $properties), options: $options);

        return Item::fromWire($r->data);
    }

    /**
     * Removes the item from the catalog. Events already recorded for it are kept.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function delete(string $itemId, array $options = []): void
    {
        $this->http->request('DELETE', '/items/' . Support::encodeSegment(Support::requireId($itemId, 'itemId')), options: $options);
    }

    /**
     * Batch upsert (1-1000). Each entry: `itemId`, `title`, optional `description` / `properties`.
     *
     * @param array<int, array<string, mixed>> $items
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function upsertMany(array $items, array $options = []): BatchResult
    {
        if ($items === []) {
            throw new ValidationException('items must be a non-empty array');
        }
        $wire = array_map(
            static fn (array $i): array => ['item_id' => Support::requireId($i['itemId'] ?? null, 'itemId')] + self::body($i['title'] ?? null, $i['description'] ?? null, $i['properties'] ?? null),
            array_values($items),
        );

        return BatchResult::fromWire($this->http->request('POST', '/items/import', body: ['items' => $wire], options: $options)->data);
    }

    /**
     * Alias of upsertMany(): the API's `POST /items/import` is a JSON batch upsert.
     *
     * @param array<int, array<string, mixed>> $items
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function import(array $items, array $options = []): BatchResult
    {
        return $this->upsertMany($items, $options);
    }

    /**
     * Batch delete (1-1000). Ids that don't exist are reported in `errors`.
     *
     * @param string[] $itemIds
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function deleteMany(array $itemIds, array $options = []): BatchResult
    {
        if ($itemIds === []) {
            throw new ValidationException('itemIds must be a non-empty array');
        }
        $ids = array_map(static fn ($id): string => Support::requireId($id, 'itemId'), array_values($itemIds));

        return BatchResult::fromWire($this->http->request('POST', '/items/delete', body: ['item_ids' => $ids], options: $options)->data);
    }

    /**
     * @param array<string, mixed>|null $properties
     * @return array<string, mixed>
     */
    private static function body(mixed $title, ?string $description, ?array $properties): array
    {
        if (!is_string($title) || $title === '') {
            throw new ValidationException('an item needs a non-empty title');
        }

        return Support::compact(['title' => $title, 'description' => $description, 'properties' => Support::properties($properties)]);
    }
}
