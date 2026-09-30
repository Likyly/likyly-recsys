<?php

declare(strict_types=1);

namespace Likyly\Http;

/** @internal */
final class Response
{
    /** @param array<string, string> $headers keys lower-cased */
    public function __construct(public readonly int $status, public readonly array $headers, public readonly mixed $data)
    {
    }
}
