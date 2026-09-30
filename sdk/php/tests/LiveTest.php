<?php

declare(strict_types=1);

namespace Likyly\Tests;

use Likyly\Exception\AuthenticationException;
use Likyly\Exception\NotFoundException;
use Likyly\Exception\PermissionDeniedException;
use Likyly\Exception\ValidationException;
use Likyly\LikylyClient;
use PHPUnit\Framework\TestCase;

/** End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless LIKYLY_TEST_* are set. */
final class LiveTest extends TestCase
{
    private const GID = 'gid://shopify/Product/123456';

    public function testFullWorkflow(): void
    {
        $url = getenv('LIKYLY_TEST_URL');
        $secret = getenv('LIKYLY_TEST_SECRET_KEY');
        $public = getenv('LIKYLY_TEST_PUBLIC_KEY');
        if (!$url || !$secret || !$public) {
            $this->markTestSkipped('LIKYLY_TEST_* not set');
        }
        $catalog = 'php-' . (int) (microtime(true) * 1000);
        $server = new LikylyClient(apiKey: $secret, baseUrl: $url, catalog: $catalog);
        $browser = new LikylyClient(apiKey: $public, baseUrl: $url, catalog: $catalog);

        $created = $server->items()->upsert('SKU-123', title: 'Nike Air Max', description: 'Running shoe', properties: ['category' => 'shoes', 'price' => 129.9]);
        $this->assertSame('SKU-123', $created->itemId);
        $this->assertEquals(129.9, $created->properties['price']);
        $server->items()->upsert(self::GID, title: 'Shopify boot', description: 'warm winter boot', properties: ['category' => 'boots']);
        $this->assertSame('Shopify boot', $server->items()->get(self::GID)->title);
        $page = $server->items()->list(limit: 1);
        $this->assertCount(1, $page->items);
        $this->assertSame(2, $page->total);
        $this->assertSame(1, $server->items()->upsertMany([['itemId' => 'SKU-A', 'title' => 'Adidas', 'description' => 'road running shoe']])->succeeded);
        $server->items()->delete('SKU-A');
        try {
            $server->items()->get('SKU-A');
            $this->fail('expected NotFoundException');
        } catch (NotFoundException) {
            $this->addToAssertionCount(1);
        }

        $this->assertSame('premium', $server->users()->upsert('user_123', properties: ['country' => 'FR', 'segment' => 'premium'])->properties['segment']);
        $this->assertNotEmpty($server->users()->list(limit: 10)->users);

        $browser->events()->view(itemId: 'SKU-123', userId: 'user_123');
        $browser->events()->view(itemId: 'SKU-123', sessionId: 'sess_123');
        $browser->events()->track('favorite', itemId: 'SKU-123', userId: 'user_123');
        $eventId = 'purchase_' . hrtime(true);
        $this->assertFalse($browser->events()->purchase(itemId: 'SKU-123', userId: 'user_123', properties: ['orderId' => 'O-1'], eventId: $eventId)->duplicate);
        $this->assertTrue($browser->events()->purchase(itemId: 'SKU-123', userId: 'user_123', properties: ['orderId' => 'O-1'], eventId: $eventId)->duplicate);
        $this->assertSame(1, $browser->events()->trackMany([['type' => 'view', 'userId' => 'user_123', 'itemId' => self::GID]])->accepted);

        $rec = $browser->recommendations()->get(userId: 'user_123', placement: 'homepage', limit: 2);
        $this->assertStringStartsWith('rec_', $rec->recommendationId);
        $this->assertSame('homepage', $rec->placement);
        $this->assertNotEmpty($rec->items);
        $this->assertSame('content', $browser->recommendations()->get(itemId: 'SKU-123', limit: 2)->strategy);
        $this->assertSame('content', $browser->recommendations()->get(itemId: self::GID, limit: 2)->strategy);
        $browser->events()->impression(itemId: $rec->items[0]->itemId, userId: 'user_123', recommendationId: $rec->recommendationId, placement: 'homepage');
        $this->assertSame('content', $browser->recommendations()->similar('SKU-123', limit: 2)->strategy);
        $this->assertSame('session', $browser->recommendations()->session(viewedItemIds: ['SKU-123'], limit: 2)->strategy);
        $this->assertNotSame('', $browser->recommendations()->popular(limit: 2)->recommendationId);

        try {
            $browser->items()->upsert('x', title: 't');
            $this->fail('expected PermissionDeniedException');
        } catch (PermissionDeniedException) {
            $this->addToAssertionCount(1);
        }
        try {
            (new LikylyClient(apiKey: 'nope', baseUrl: $url))->events()->view(itemId: 'i', userId: 'u');
            $this->fail('expected AuthenticationException');
        } catch (AuthenticationException) {
            $this->addToAssertionCount(1);
        }
        try {
            $server->items()->list(limit: 5000);
            $this->fail('expected ValidationException');
        } catch (ValidationException $e) {
            $this->assertStringStartsWith('req_', (string) $e->requestId);
        }

        $server->items()->deleteMany(['SKU-123', self::GID]);
        $server->users()->delete('user_123');
    }
}
