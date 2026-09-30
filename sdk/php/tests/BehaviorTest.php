<?php

declare(strict_types=1);

namespace Likyly\Tests;

use GuzzleHttp\Exception\ConnectException;
use GuzzleHttp\Psr7\Request;
use GuzzleHttp\Psr7\Response;
use Likyly\Exception\ApiException;
use Likyly\Exception\AuthenticationException;
use Likyly\Exception\LikylyException;
use Likyly\Exception\NetworkException;
use Likyly\Exception\RateLimitException;
use Likyly\Exception\TimeoutException;
use Likyly\Exception\ValidationException;
use Likyly\LikylyClient;
use PHPUnit\Framework\Attributes\DataProvider;

final class BehaviorTest extends TestCase
{
    private const REC = ['recommendation_id' => 'rec_1', 'strategy' => 'popular', 'items' => []];
    private const EVT = ['message' => 'ok', 'event_id' => null, 'duplicate' => false];

    public function testRequiresAnApiKey(): void
    {
        $this->expectException(ValidationException::class);
        new LikylyClient(apiKey: '  ');
    }

    public function testDefaultsToTheProductionApiAndAppendsAUserAgent(): void
    {
        $client = $this->client([self::json(200, [])], ['baseUrl' => null, 'userAgent' => 'my-shop/1.4']);
        $client->items()->list();
        $this->assertSame('api.likyly.com', $this->request()->getUri()->getHost());
        $this->assertMatchesRegularExpression('#^likyly-php/\d+\.\d+\.\d+ my-shop/1\.4$#', $this->request()->getHeaderLine('User-Agent'));
    }

    public function testAnEventNeedsAnItemAndAUserOrASession(): void
    {
        $client = $this->client([self::json(200, self::EVT)]);
        foreach ([fn () => $client->events()->view(itemId: 'a'), fn () => $client->events()->view(itemId: '', userId: 'u')] as $call) {
            try {
                $call();
                $this->fail('expected ValidationException');
            } catch (ValidationException) {
                $this->addToAssertionCount(1);
            }
        }
        $this->assertCount(0, $this->history);
    }

    public function testIdsAreStringsNeverCoerced(): void
    {
        $client = $this->client([self::json(200, self::EVT)]);
        $this->expectException(\TypeError::class); // strict_types: an int is not a string id
        $client->items()->get(7); // @phpstan-ignore-line
    }

    public function testEventTypesAreOpenButUrlSafe(): void
    {
        $client = $this->client([self::json(200, self::EVT)]);
        $client->events()->track('favorite', itemId: 'i', userId: 'u');
        $this->expectException(ValidationException::class);
        $client->events()->track('bad type!', itemId: 'i', userId: 'u');
    }

    public function testAdvancedEndpointsRefuseAnIdContainingASlash(): void
    {
        $client = $this->client([self::json(200, self::REC)]);
        try {
            $client->recommendations()->similar(itemId: 'gid://shopify/Product/1');
            $this->fail('expected ValidationException');
        } catch (ValidationException $e) {
            $this->assertStringContainsString('recommendations()->get()', $e->getMessage());
        }
        $client->recommendations()->get(itemId: 'gid://shopify/Product/1'); // the body-based call accepts it
        $this->assertCount(1, $this->history);
    }

    public function testSessionNeedsExactlyOneOfListOrUser(): void
    {
        $client = $this->client([self::json(200, self::REC)]);
        foreach ([[], ['viewedItemIds' => ['a'], 'userId' => 'u'], ['viewedItemIds' => []]] as $args) {
            try {
                $client->recommendations()->session(...$args);
                $this->fail('expected ValidationException');
            } catch (ValidationException) {
                $this->addToAssertionCount(1);
            }
        }
    }

    public function testBatchesMustNotBeEmpty(): void
    {
        $client = $this->client([self::json(200, self::EVT)]);
        $this->expectException(ValidationException::class);
        $client->items()->upsertMany([]);
    }

    public function testDateTimeIsSerializedToIso8601(): void
    {
        $client = $this->client([self::json(200, self::EVT)]);
        $client->events()->view(itemId: 'i', userId: 'u', occurredAt: new \DateTimeImmutable('2026-09-24T10:30:00+00:00'));
        $this->assertSame('2026-09-24T10:30:00+00:00', json_decode((string) $this->request()->getBody(), true)['occurred_at']);
    }

    public function testPropertiesKeysAreNeverRenamedAndEmptyPropertiesStayAnObject(): void
    {
        $client = $this->client([self::json(200, self::EVT), self::json(200, ['item_id' => 'a', 'title' => 't'])]);
        $client->events()->purchase(itemId: 'i', userId: 'u', properties: ['orderId' => 'O-1', 'nested' => ['snakeCase_and_camelCase' => 1]]);
        $this->assertSame(['orderId' => 'O-1', 'nested' => ['snakeCase_and_camelCase' => 1]], json_decode((string) $this->request(0)->getBody(), true)['properties']);
        $client->items()->upsert('a', title: 't', properties: []);
        $this->assertStringContainsString('"properties":{}', (string) $this->request(1)->getBody());
    }

    public function testTheHierarchyIsUsable(): void
    {
        $client = $this->client([self::json(401, ['detail' => 'nope', 'request_id' => 'req_x'])], ['maxRetries' => 0]);
        try {
            $client->items()->get('a');
            $this->fail('expected an exception');
        } catch (AuthenticationException $e) {
            $this->assertInstanceOf(ApiException::class, $e);
            $this->assertInstanceOf(LikylyException::class, $e);
            $this->assertInstanceOf(\RuntimeException::class, $e);
            $this->assertSame('req_x', $e->requestId);
        }
    }

    public function testA422ListsTheOffendingFields(): void
    {
        $client = $this->client([self::json(422, ['detail' => [['loc' => ['body', 'title'], 'msg' => 'Field required']]])], ['maxRetries' => 0]);
        $this->expectException(ValidationException::class);
        $this->expectExceptionMessage('Field required');
        $client->items()->get('a');
    }

    public function testANonJsonErrorBodyStillBecomesAnApiException(): void
    {
        $client = $this->client([new Response(502, [], '<html>Bad gateway</html>')], ['maxRetries' => 0]);
        try {
            $client->items()->get('a');
            $this->fail('expected an exception');
        } catch (ApiException $e) {
            $this->assertSame(502, $e->statusCode);
        }
    }

    public function testNetworkFailureAndTimeout(): void
    {
        $client = $this->client([new ConnectException('boom', new Request('GET', '/'))], ['maxRetries' => 0]);
        $this->expectException(NetworkException::class);
        $client->items()->get('a');
    }

    public function testATimeoutBecomesTimeoutException(): void
    {
        $client = $this->client([new ConnectException('cURL error 28: Operation timed out', new Request('GET', '/'), null, ['errno' => 28])], ['maxRetries' => 0]);
        $this->expectException(TimeoutException::class);
        $client->items()->get('a');
    }

    public function test429IsRetriedForEveryRequestHonoringRetryAfter(): void
    {
        $client = $this->client([self::json(429, ['detail' => 'slow'], ['Retry-After' => '3']), self::json(200, self::EVT)]);
        $this->assertFalse($client->events()->view(itemId: 'i', userId: 'u')->duplicate); // no eventId: still safe - 429 never reached the app
        $this->assertCount(2, $this->history);
        $this->assertSame([3.0], $this->sleeps);
    }

    public function testARetryAfterBeyondAMinuteIsNotWaitedFor(): void
    {
        $client = $this->client([self::json(429, ['detail' => 'x'], ['Retry-After' => '600'])]);
        try {
            $client->items()->get('a');
            $this->fail('expected RateLimitException');
        } catch (RateLimitException $e) {
            $this->assertSame(600.0, $e->retryAfter);
            $this->assertCount(1, $this->history);
        }
    }

    public function testExponentialBackoffWithJitterThenGivesUp(): void
    {
        $client = $this->client(array_fill(0, 4, self::json(503, ['detail' => 'down'])), ['maxRetries' => 3]);
        try {
            $client->items()->get('a');
            $this->fail('expected an exception');
        } catch (ApiException) {
            $this->assertCount(4, $this->history);
            $this->assertSame([0.5, 1.0, 2.0], $this->sleeps);
        }
    }

    /** @return array<string, array{0: int}> */
    public static function gatewayStatuses(): array
    {
        return ['502' => [502], '503' => [503], '504' => [504]];
    }

    #[DataProvider('gatewayStatuses')]
    public function testIdempotentCallsRetryOnGatewayErrors(int $status): void
    {
        $client = $this->client([self::json($status, ['detail' => 'x']), self::json(200, ['item_id' => 'a', 'title' => 't'])]);
        $client->items()->upsert('a', title: 't');
        $this->assertCount(2, $this->history);
    }

    public function testAnEventWithoutEventIdIsNeverRetriedOnAnAmbiguousFailure(): void
    {
        foreach ([self::json(503, ['detail' => 'x']), new ConnectException('reset', new Request('GET', '/')), new ConnectException('timed out', new Request('GET', '/'), null, ['errno' => 28])] as $first) {
            $client = $this->client([$first, self::json(200, self::EVT)]);
            try {
                $client->events()->purchase(itemId: 'i', userId: 'u');
                $this->fail('expected an exception');
            } catch (LikylyException) {
                $this->assertCount(1, $this->history);
            }
        }
    }

    public function testAnEventWithEventIdIsRetriedTheReplayIsHarmless(): void
    {
        $client = $this->client([self::json(503, ['detail' => 'x']), self::json(200, ['message' => 'dup', 'event_id' => 'e1', 'duplicate' => true])]);
        $this->assertTrue($client->events()->purchase(itemId: 'i', userId: 'u', eventId: 'e1')->duplicate);
        $this->assertCount(2, $this->history);
    }

    public function testTrackManyIsRetriedOnlyIfEveryEventHasAnEventId(): void
    {
        $batch = ['received' => 2, 'accepted' => 2, 'duplicates' => 0];
        $client = $this->client([self::json(503, ['detail' => 'x']), self::json(200, $batch)]);
        try {
            $client->events()->trackMany([['type' => 'view', 'userId' => 'u', 'itemId' => '1'], ['type' => 'view', 'userId' => 'u', 'itemId' => '2', 'eventId' => 'e']]);
            $this->fail('expected an exception');
        } catch (ApiException) {
            $this->assertCount(1, $this->history);
        }
        $client = $this->client([self::json(503, ['detail' => 'x']), self::json(200, $batch)]);
        $client->events()->trackMany([['type' => 'view', 'userId' => 'u', 'itemId' => '1', 'eventId' => 'a'], ['type' => 'view', 'userId' => 'u', 'itemId' => '2', 'eventId' => 'b']]);
        $this->assertCount(2, $this->history);
    }

    /** @return array<string, array{0: int}> */
    public static function clientErrors(): array
    {
        return ['400' => [400], '401' => [401], '403' => [403], '404' => [404], '422' => [422]];
    }

    #[DataProvider('clientErrors')]
    public function testClientErrorsAreNeverRetried(int $status): void
    {
        $client = $this->client([self::json($status, ['detail' => 'x']), self::json(200, [])]);
        try {
            $client->items()->get('a');
            $this->fail('expected an exception');
        } catch (LikylyException) {
            $this->assertCount(1, $this->history);
        }
    }

    public function testPerCallMaxRetriesOverridesTheClient(): void
    {
        $client = $this->client([self::json(503, ['detail' => 'x'])], ['maxRetries' => 5]);
        try {
            $client->items()->get('a', ['maxRetries' => 0]);
            $this->fail('expected an exception');
        } catch (ApiException) {
            $this->assertCount(1, $this->history);
        }
    }

    public function testListsReportTheTotalAndOnlySendWhatWasAskedFor(): void
    {
        $client = $this->client([self::json(200, [['item_id' => 'a', 'title' => 'A']], ['X-Total-Count' => '42']), self::json(200, [])]);
        $page = $client->items()->list(limit: 1, offset: 10);
        $this->assertSame([42, 1, 10], [$page->total, $page->limit, $page->offset]);
        $client->items()->list();
        $this->assertSame('', $this->request(1)->getUri()->getQuery());
    }

    public function testImportIsAnAliasOfUpsertMany(): void
    {
        $client = $this->client([self::json(200, ['received' => 1, 'succeeded' => 1, 'failed' => 0])]);
        $client->items()->import([['itemId' => 'a', 'title' => 'A']]);
        $this->assertSame('/items/import', $this->request()->getUri()->getPath());
    }
}
