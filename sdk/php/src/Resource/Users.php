<?php

declare(strict_types=1);

namespace Likyly\Resource;

use Likyly\Exception\ValidationException;
use Likyly\Http\HttpClient;
use Likyly\Internal\Support;
use Likyly\Model\BatchResult;
use Likyly\Model\User;
use Likyly\Model\UserList;

/**
 * `$likyly->users()` - optional user profiles. Needs the **secret** API key (profiles are personal data).
 * You don't have to create a user before sending events for them.
 */
final class Users
{
    public function __construct(private readonly HttpClient $http)
    {
    }

    /**
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function get(string $userId, array $options = []): User
    {
        $r = $this->http->request('GET', '/users/' . Support::encodeSegment(Support::requireId($userId, 'userId')), options: $options);

        return User::fromWire($r->data);
    }

    /**
     * One page of users. Without `limit` the API returns every user.
     *
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function list(?int $limit = null, ?int $offset = null, array $options = []): UserList
    {
        $r = $this->http->request('GET', '/users', ['limit' => $limit, 'offset' => $offset], options: $options);
        $total = $r->headers['x-total-count'] ?? null;

        return new UserList(
            array_map(static fn (array $w) => User::fromWire($w), $r->data),
            $total === null ? null : (int) $total,
            $limit,
            $offset ?? 0,
        );
    }

    /**
     * Creates or replaces the profile (idempotent). `properties` is free-form: country, segment, language, ...
     *
     * @param array<string, mixed>|null $properties
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function upsert(string $userId, ?array $properties = null, array $options = []): User
    {
        $body = Support::compact(['properties' => Support::properties($properties)]);
        $r = $this->http->request('PUT', '/users/' . Support::encodeSegment(Support::requireId($userId, 'userId')), body: $body === [] ? new \stdClass() : $body, options: $options);

        return User::fromWire($r->data);
    }

    /**
     * Erases the user: the profile **and every event recorded for them**.
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function delete(string $userId, array $options = []): void
    {
        $this->http->request('DELETE', '/users/' . Support::encodeSegment(Support::requireId($userId, 'userId')), options: $options);
    }

    /**
     * Batch upsert (1-1000). Each entry: `userId` and optional `properties`.
     *
     * @param array<int, array<string, mixed>> $users
     * @param array{timeout?: float, maxRetries?: int} $options
     */
    public function import(array $users, array $options = []): BatchResult
    {
        if ($users === []) {
            throw new ValidationException('users must be a non-empty array');
        }
        $wire = array_map(
            static fn (array $u): array => ['user_id' => Support::requireId($u['userId'] ?? null, 'userId')] + Support::compact(['properties' => Support::properties($u['properties'] ?? null)]),
            array_values($users),
        );

        return BatchResult::fromWire($this->http->request('POST', '/users/import', body: ['users' => $wire], options: $options)->data);
    }
}
