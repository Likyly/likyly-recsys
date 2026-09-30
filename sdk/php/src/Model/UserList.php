<?php

declare(strict_types=1);

namespace Likyly\Model;

final class UserList
{
    /** @param User[] $users */
    public function __construct(
        public readonly array $users,
        public readonly ?int $total = null,
        public readonly ?int $limit = null,
        public readonly int $offset = 0,
    ) {
    }
}
