<?php

declare(strict_types=1);

namespace Likyly\Internal;

use Likyly\Exception\ValidationException;

/** @internal */
final class Support
{
    /**
     * Percent-encodes a path segment: everything outside RFC 3986 "unreserved" (A-Z a-z 0-9 - . _ ~)
     * becomes %XX (UTF-8), '/' included - so "gid://shopify/Product/1" is one segment.
     */
    public static function encodeSegment(string $value): string
    {
        return rawurlencode($value);
    }

    /** Ids are opaque, non-empty strings - never coerced to numbers. */
    public static function requireId(mixed $value, string $name): string
    {
        if (!is_string($value) || trim($value) === '') {
            throw new ValidationException("{$name} must be a non-empty string (your own identifier, e.g. \"SKU-123\")");
        }

        return $value;
    }

    /** Advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used. */
    public static function requirePathSafeId(mixed $value, string $name): string
    {
        $id = self::requireId($value, $name);
        if (str_contains($id, '/')) {
            throw new ValidationException(
                "{$name} \"{$id}\" contains \"/\", which this endpoint cannot carry in its URL path - use recommendations()->get(), which takes ids in the request body",
            );
        }

        return $id;
    }

    /**
     * @param array<string, mixed> $values
     * @return array<string, mixed> the entries that are not null
     */
    public static function compact(array $values): array
    {
        return array_filter($values, static fn ($v) => $v !== null);
    }

    /**
     * `properties` must reach the API as a JSON *object*: an empty PHP array would encode as `[]`.
     *
     * @param array<string, mixed>|null $properties
     * @return array<string, mixed>|\stdClass|null
     */
    public static function properties(?array $properties): array|\stdClass|null
    {
        if ($properties === null) {
            return null;
        }

        return $properties === [] ? new \stdClass() : $properties;
    }

    public static function limit(?int $limit, int $default = 10): int
    {
        $value = $limit ?? $default;
        if ($value < 1) {
            throw new ValidationException('limit must be a positive integer');
        }

        return $value;
    }
}
