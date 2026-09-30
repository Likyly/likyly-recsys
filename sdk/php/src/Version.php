<?php

declare(strict_types=1);

namespace Likyly;

final class Version
{
    public const SDK = '1.0.0';
    public const DEFAULT_BASE_URL = 'https://api.likyly.com';
    public const USER_AGENT = 'likyly-php/' . self::SDK;
}
