<?php

declare(strict_types=1);

namespace Likyly\Resource;

use Likyly\Exception\ValidationException;
use Likyly\Http\HttpClient;
use Likyly\Internal\Support;
use Likyly\Model\EventBatchResult;
use Likyly\Model\EventResult;

/**
 * `$likyly->events()` - what your visitors do. Works with the **public** key from a browser-facing
 * service, or the secret key. `track()` is the one mechanism; `view()`, `click()`, ... are shortcuts
 * that call it with the matching event type. Event types are open strings (`favorite`, `share`, ...):
 * the six helpers are conveniences, not a closed list.
 *
 * A failed call is only retried automatically when it carries an `eventId` (then a replay is
 * harmless); without one, an ambiguous failure is thrown rather than risking a duplicate.
 */
final class Events
{
    public function __construct(private readonly HttpClient $http)
    {
    }

    /**
     * Needs `itemId` and a `userId` and/or a `sessionId` (anonymous visitor).
     *
     * @param array<string, mixed>|null $properties price, currency, orderId, ... (keys are yours, never renamed)
     * @param string|\DateTimeInterface|null $occurredAt ISO 8601; defaults to the server's time
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function track(
        string $type,
        string $itemId,
        ?string $userId = null,
        ?string $sessionId = null,
        ?string $recommendationId = null,
        ?string $placement = null,
        ?int $quantity = null,
        string|\DateTimeInterface|null $occurredAt = null,
        ?array $properties = null,
        ?string $eventId = null,
        array $options = [],
    ): EventResult {
        $body = self::wire($itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId);
        $r = $this->http->request('POST', '/events/' . Support::encodeSegment(self::type($type)), body: $body, idempotent: $eventId !== null, options: $options);

        return EventResult::fromWire($r->data);
    }

    /**
     * Up to 1000 events. Each entry has `type` plus the fields of track() (`itemId`, `userId`, ...).
     *
     * @param array<int, array<string, mixed>> $events
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function trackMany(array $events, array $options = []): EventBatchResult
    {
        if ($events === []) {
            throw new ValidationException('events must be a non-empty array');
        }
        $wire = [];
        $allIdempotent = true;
        foreach (array_values($events) as $e) {
            $wire[] = ['event_type' => self::type($e['type'] ?? null)] + self::wire(
                $e['itemId'] ?? null,
                $e['userId'] ?? null,
                $e['sessionId'] ?? null,
                $e['recommendationId'] ?? null,
                $e['placement'] ?? null,
                $e['quantity'] ?? null,
                $e['occurredAt'] ?? null,
                $e['properties'] ?? null,
                $e['eventId'] ?? null,
            );
            $allIdempotent = $allIdempotent && isset($e['eventId']);
        }
        $r = $this->http->request('POST', '/events/batch', body: ['events' => $wire], idempotent: $allIdempotent, options: $options);

        return new EventBatchResult((int) $r->data['received'], (int) $r->data['accepted'], (int) $r->data['duplicates']);
    }

    /**
     * The item was shown to the visitor (send the `recommendationId` it came with).
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function impression(string $itemId, ?string $userId = null, ?string $sessionId = null, ?string $recommendationId = null, ?string $placement = null, ?int $quantity = null, string|\DateTimeInterface|null $occurredAt = null, ?array $properties = null, ?string $eventId = null, array $options = []): EventResult
    {
        return $this->track('impression', $itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId, $options);
    }

    /**
     * The visitor looked at the item (a product page, an article).
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function view(string $itemId, ?string $userId = null, ?string $sessionId = null, ?string $recommendationId = null, ?string $placement = null, ?int $quantity = null, string|\DateTimeInterface|null $occurredAt = null, ?array $properties = null, ?string $eventId = null, array $options = []): EventResult
    {
        return $this->track('view', $itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId, $options);
    }

    /**
     * The visitor clicked the item.
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function click(string $itemId, ?string $userId = null, ?string $sessionId = null, ?string $recommendationId = null, ?string $placement = null, ?int $quantity = null, string|\DateTimeInterface|null $occurredAt = null, ?array $properties = null, ?string $eventId = null, array $options = []): EventResult
    {
        return $this->track('click', $itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId, $options);
    }

    /**
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function addToCart(string $itemId, ?string $userId = null, ?string $sessionId = null, ?string $recommendationId = null, ?string $placement = null, ?int $quantity = null, string|\DateTimeInterface|null $occurredAt = null, ?array $properties = null, ?string $eventId = null, array $options = []): EventResult
    {
        return $this->track('add_to_cart', $itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId, $options);
    }

    /**
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function removeFromCart(string $itemId, ?string $userId = null, ?string $sessionId = null, ?string $recommendationId = null, ?string $placement = null, ?int $quantity = null, string|\DateTimeInterface|null $occurredAt = null, ?array $properties = null, ?string $eventId = null, array $options = []): EventResult
    {
        return $this->track('remove_from_cart', $itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId, $options);
    }

    /**
     * Set `eventId` (e.g. `purchase_<orderId>_<itemId>`) so a retry can never count the purchase twice.
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function purchase(string $itemId, ?string $userId = null, ?string $sessionId = null, ?string $recommendationId = null, ?string $placement = null, ?int $quantity = null, string|\DateTimeInterface|null $occurredAt = null, ?array $properties = null, ?string $eventId = null, array $options = []): EventResult
    {
        return $this->track('purchase', $itemId, $userId, $sessionId, $recommendationId, $placement, $quantity, $occurredAt, $properties, $eventId, $options);
    }

    private static function type(mixed $type): string
    {
        if (!is_string($type) || preg_match('/^[A-Za-z0-9_-]{1,64}$/', $type) !== 1) {
            throw new ValidationException('event type must be 1-64 characters of letters, digits, "_" or "-" (e.g. "view", "add_to_cart", "favorite")');
        }

        return $type;
    }

    /**
     * @param array<string, mixed>|null $properties
     * @return array<string, mixed>
     */
    private static function wire(mixed $itemId, mixed $userId, mixed $sessionId, ?string $recommendationId, ?string $placement, ?int $quantity, string|\DateTimeInterface|null $occurredAt, ?array $properties, ?string $eventId): array
    {
        Support::requireId($itemId, 'itemId');
        if ($userId === null && $sessionId === null) {
            throw new ValidationException('an event needs a userId or a sessionId (or both)');
        }
        if ($userId !== null) {
            Support::requireId($userId, 'userId');
        }
        if ($sessionId !== null) {
            Support::requireId($sessionId, 'sessionId');
        }

        return Support::compact([
            'event_id' => $eventId,
            'user_id' => $userId,
            'session_id' => $sessionId,
            'item_id' => $itemId,
            'recommendation_id' => $recommendationId,
            'placement' => $placement,
            'quantity' => $quantity,
            'occurred_at' => $occurredAt instanceof \DateTimeInterface ? $occurredAt->format(\DATE_ATOM) : $occurredAt,
            'properties' => Support::properties($properties),
        ]);
    }
}
