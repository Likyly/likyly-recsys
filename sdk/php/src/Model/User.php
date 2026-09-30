<?php

declare(strict_types=1);

namespace Likyly\Model;

final class User
{
    /** @param array<string, mixed> $properties */
    public function __construct(
        public readonly string $userId,
        public readonly array $properties = [],
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self((string) $w['user_id'], $w['properties'] ?? []);
    }
}
