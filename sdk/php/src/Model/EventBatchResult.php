<?php

declare(strict_types=1);

namespace Likyly\Model;

final class EventBatchResult
{
    /** @param int $duplicates events skipped because their eventId was already recorded */
    public function __construct(
        public readonly int $received,
        public readonly int $accepted,
        public readonly int $duplicates,
    ) {
    }
}
