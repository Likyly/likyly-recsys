<?php

declare(strict_types=1);

namespace Likyly\Model;

final class SimilarUser
{
    /** @param string[] $sharedItemIds */
    public function __construct(public readonly string $userId, public readonly array $sharedItemIds = [])
    {
    }
}
