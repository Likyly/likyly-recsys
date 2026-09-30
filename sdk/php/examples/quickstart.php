<?php

declare(strict_types=1);

// Quick start: catalog -> events -> recommendations -> attribution.
// Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... php examples/quickstart.php

require __DIR__ . '/../vendor/autoload.php';

// region:imports
use Likyly\Exception\NotFoundException;
use Likyly\Exception\RateLimitException;
use Likyly\LikylyClient;
// endregion

// region:initialize
// Backend only: the secret key gives access to your catalog and your users.
$likyly = new LikylyClient(
    apiKey: getenv('LIKYLY_SECRET_KEY'),
    baseUrl: getenv('LIKYLY_BASE_URL') ?: null, // docs:omit
    catalog: getenv('LIKYLY_CATALOG') ?: null, // docs:omit
);
// endregion

// region:catalog
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
$likyly->items()->upsertMany([
    ['itemId' => 'SKU-1', 'title' => 'Nike Air Max', 'description' => 'Running shoe with air cushioning', 'properties' => ['category' => 'shoes', 'brand' => 'Nike', 'price' => 129.9]],
    ['itemId' => 'SKU-2', 'title' => 'Adidas Ultraboost', 'description' => 'Responsive running shoe', 'properties' => ['category' => 'shoes', 'brand' => 'Adidas', 'price' => 149]],
    ['itemId' => 'SKU-3', 'title' => 'Nike Pegasus', 'description' => 'Everyday running shoe', 'properties' => ['category' => 'shoes', 'brand' => 'Nike', 'price' => 119]],
]);

// Users are optional: describe them if you want the profile to travel with their events.
$likyly->users()->upsert('user_123', properties: ['country' => 'FR', 'segment' => 'premium']);
// endregion

// region:track
// Tell LIKYLY what your visitors do.
$likyly->events()->view(userId: 'user_123', itemId: 'SKU-1');

// Purchases carry an eventId: replaying the call can never count the sale twice.
$likyly->events()->purchase(
    eventId: 'purchase_order_9281_SKU-1',
    userId: 'user_123',
    itemId: 'SKU-1',
    quantity: 1,
    properties: ['price' => 129.9, 'currency' => 'EUR', 'orderId' => 'order_9281'],
);
// endregion

// region:recommend
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
$recs = $likyly->recommendations()->get(userId: 'user_123', placement: 'homepage', limit: 3);

echo "strategy: {$recs->strategy}\n";
foreach ($recs->items as $item) {
    echo $item->itemId, ' ', $item->title, "\n";
}
// endregion

// region:showcase
// Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
$productRecs = $likyly->recommendations()->get(
    userId: 'user_123', // visiteur connecté
    sessionId: 'sess_abc', // ou visiteur anonyme (cookie)
    itemId: 'SKU-1', // fiche produit en cours de consultation
    placement: 'product_page', // où elles seront affichées (libre)
    limit: 6, // combien d'articles (10 par défaut)
);

foreach ($productRecs->items as $item) {
    echo $item->itemId, ' ', $item->title, ' ', $item->score, "\n";
}
// endregion

// region:attribution
// Send the recommendationId back: LIKYLY measures which recommendations get seen, clicked and bought.
$likyly->events()->impression(userId: 'user_123', itemId: $recs->items[0]->itemId, recommendationId: $recs->recommendationId, placement: 'homepage');
$likyly->events()->click(userId: 'user_123', itemId: $recs->items[0]->itemId, recommendationId: $recs->recommendationId, placement: 'homepage');
// endregion

// region:browser
// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
$front = new LikylyClient(
    apiKey: getenv('LIKYLY_PUBLIC_KEY'),
    baseUrl: getenv('LIKYLY_BASE_URL') ?: null, // docs:omit
    catalog: getenv('LIKYLY_CATALOG') ?: null, // docs:omit
);

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
$front->events()->view(sessionId: 'sess_abc', itemId: 'SKU-2');
$forVisitor = $front->recommendations()->get(sessionId: 'sess_abc', viewedItemIds: ['SKU-2'], placement: 'product_page', limit: 3);
// endregion

// region:custom
// Any string is a valid event type: track what matters to your business.
$likyly->events()->track('favorite', itemId: 'SKU-2', userId: 'user_123', properties: ['list' => 'wishlist']);

// Up to 1000 events per call, each with its own type.
$likyly->events()->trackMany([
    ['type' => 'view', 'userId' => 'user_123', 'itemId' => 'SKU-3'],
    ['type' => 'add_to_cart', 'userId' => 'user_123', 'itemId' => 'SKU-3', 'quantity' => 1],
]);
// endregion

// region:advanced
// Advanced Recommendations: one strategy at a time, when you want to choose.
$similar = $likyly->recommendations()->similar(itemId: 'SKU-1', limit: 3);
$hybrid = $likyly->recommendations()->hybrid(userId: 'user_123', itemId: 'SKU-1', alpha: 0.7, limit: 3);
$session = $likyly->recommendations()->session(viewedItemIds: ['SKU-1', 'SKU-2'], limit: 3);
// endregion

// region:config
$tuned = new LikylyClient(
    apiKey: getenv('LIKYLY_SECRET_KEY'),
    timeout: 5.0, // seconds per attempt (default 10)
    maxRetries: 3, // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    userAgent: 'my-shop/1.4', // appended to the SDK's User-Agent
    baseUrl: getenv('LIKYLY_BASE_URL') ?: null, // docs:omit
);
// endregion

// region:errors
try {
    $likyly->items()->get('does-not-exist');
} catch (NotFoundException $e) {
    echo 'no such item, request ', $e->requestId, "\n";
} catch (RateLimitException $e) {
    echo 'slow down, retry in ', $e->retryAfter, " s\n";
}
// endregion

echo "visitor strategy: {$forVisitor->strategy}\n";

// region:cleanup
$likyly->items()->deleteMany(['SKU-1', 'SKU-2', 'SKU-3']);
$likyly->users()->delete('user_123');
// endregion
echo "quickstart ok\n";
