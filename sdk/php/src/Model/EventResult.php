<?php

declare(strict_types=1);

namespace Likyly\Model;

final class EventResult
{
    /** @param bool $duplicate true if this eventId was already recorded - nothing was written */
    public function __construct(
        public readonly string $message,
        public readonly bool $duplicate = false,
        public readonly ?string $eventId = null,
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self((string) $w['message'], ($w['duplicate'] ?? false) === true, $w['event_id'] ?? null);
    }
}
