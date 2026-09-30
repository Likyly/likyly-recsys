<?php

declare(strict_types=1);

namespace Likyly\Model;

/** A failing entry is reported in $errors, it does not throw. */
final class BatchResult
{
    /** @param array<int, array{index: int, id: ?string, message: string}> $errors */
    public function __construct(
        public readonly int $received,
        public readonly int $succeeded,
        public readonly int $failed,
        public readonly array $errors = [],
    ) {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        $errors = array_map(
            static fn (array $e): array => ['index' => (int) $e['index'], 'id' => $e['id'] ?? null, 'message' => (string) $e['message']],
            $w['errors'] ?? [],
        );

        return new self((int) $w['received'], (int) $w['succeeded'], (int) $w['failed'], $errors);
    }
}
