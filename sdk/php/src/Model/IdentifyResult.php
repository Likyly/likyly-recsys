<?php

declare(strict_types=1);

namespace Likyly\Model;

final class IdentifyResult
{
    /** @param int $linkedInteractions how many of the anonymous session's past interactions now count for the user */
    public function __construct(public readonly int $linkedInteractions)
    {
    }

    /** @param array<string, mixed> $w */
    public static function fromWire(array $w): self
    {
        return new self((int) $w['linked_interactions']);
    }
}
