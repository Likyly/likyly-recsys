// Quick start: catalog -> events -> recommendations -> attribution.
//
// Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... dotnet run --project examples/Quickstart
// region:imports
using Likyly;
// endregion

// region:initialize
// Backend only: the secret key gives access to your catalog and your users.
var likyly = new LikylyClient(new LikylyClientOptions
{
    ApiKey = Environment.GetEnvironmentVariable("LIKYLY_SECRET_KEY")!,
    BaseUrl = Environment.GetEnvironmentVariable("LIKYLY_BASE_URL") ?? LikylyClient.DefaultBaseUrl, // docs:omit
    Catalog = Environment.GetEnvironmentVariable("LIKYLY_CATALOG"), // docs:omit
});
// endregion

// region:catalog
// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
await likyly.Items.UpsertManyAsync(
[
    new ItemImport { ItemId = "SKU-1", Title = "Nike Air Max", Description = "Running shoe with air cushioning", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["brand"] = "Nike", ["price"] = 129.9 } },
    new ItemImport { ItemId = "SKU-2", Title = "Adidas Ultraboost", Description = "Responsive running shoe", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["brand"] = "Adidas", ["price"] = 149 } },
    new ItemImport { ItemId = "SKU-3", Title = "Nike Pegasus", Description = "Everyday running shoe", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["brand"] = "Nike", ["price"] = 119 } },
]);

// Users are optional: describe them if you want the profile to travel with their events.
await likyly.Users.UpsertAsync("user_123", new UserInput { Properties = new Dictionary<string, object?> { ["country"] = "FR", ["segment"] = "premium" } });
// endregion

// region:track
// Tell LIKYLY what your visitors do.
await likyly.Events.ViewAsync(new EventInput { UserId = "user_123", ItemId = "SKU-1" });

// Purchases carry an EventId: replaying the call can never count the sale twice.
await likyly.Events.PurchaseAsync(new EventInput
{
    EventId = "purchase_order_9281_SKU-1",
    UserId = "user_123",
    ItemId = "SKU-1",
    Quantity = 1,
    Properties = new Dictionary<string, object?> { ["price"] = 129.9, ["currency"] = "EUR", ["orderId"] = "order_9281" },
});
// endregion

// region:recommend
// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
var recs = await likyly.Recommendations.GetAsync(new RecommendationRequest { UserId = "user_123", Placement = "homepage", Limit = 3 });

Console.WriteLine($"strategy: {recs.Strategy}");
foreach (var item in recs.Items)
{
    Console.WriteLine($"{item.ItemId} {item.Title}");
}
// endregion

// region:showcase
// Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
var productRecs = await likyly.Recommendations.GetAsync(new RecommendationRequest
{
    UserId = "user_123", // visiteur connecté
    SessionId = "sess_abc", // ou visiteur anonyme (cookie)
    ItemId = "SKU-1", // fiche produit en cours de consultation
    Placement = "product_page", // où elles seront affichées (libre)
    Limit = 6, // combien d'articles (10 par défaut)
});

foreach (var item in productRecs.Items)
{
    Console.WriteLine($"{item.ItemId} {item.Title} {item.Score}");
}
// endregion

// region:attribution
// Send the RecommendationId back: LIKYLY measures which recommendations get seen, clicked and bought.
var shown = new EventInput { UserId = "user_123", ItemId = recs.Items[0].ItemId, RecommendationId = recs.RecommendationId, Placement = "homepage" };
await likyly.Events.ImpressionAsync(shown);
await likyly.Events.ClickAsync(shown);
// endregion

// region:browser
// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
var front = new LikylyClient(new LikylyClientOptions
{
    ApiKey = Environment.GetEnvironmentVariable("LIKYLY_PUBLIC_KEY")!,
    BaseUrl = Environment.GetEnvironmentVariable("LIKYLY_BASE_URL") ?? LikylyClient.DefaultBaseUrl, // docs:omit
    Catalog = Environment.GetEnvironmentVariable("LIKYLY_CATALOG"), // docs:omit
});

// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
await front.Events.ViewAsync(new EventInput { SessionId = "sess_abc", ItemId = "SKU-2" });
var forVisitor = await front.Recommendations.GetAsync(new RecommendationRequest { SessionId = "sess_abc", ViewedItemIds = ["SKU-2"], Placement = "product_page", Limit = 3 });
// endregion

// region:custom
// Any string is a valid event type: track what matters to your business.
await likyly.Events.TrackAsync("favorite", new EventInput { UserId = "user_123", ItemId = "SKU-2", Properties = new Dictionary<string, object?> { ["list"] = "wishlist" } });

// Up to 1000 events per call, each with its own type.
await likyly.Events.TrackManyAsync(
[
    new TypedEventInput { Type = "view", UserId = "user_123", ItemId = "SKU-3" },
    new TypedEventInput { Type = "add_to_cart", UserId = "user_123", ItemId = "SKU-3", Quantity = 1 },
]);
// endregion

// region:advanced
// Advanced Recommendations: one strategy at a time, when you want to choose.
var similar = await likyly.Recommendations.SimilarAsync(new SimilarOptions { ItemId = "SKU-1", Limit = 3 });
var hybrid = await likyly.Recommendations.HybridAsync(new HybridOptions { UserId = "user_123", ItemId = "SKU-1", Alpha = 0.7, Limit = 3 });
var session = await likyly.Recommendations.SessionAsync(new SessionOptions { ViewedItemIds = ["SKU-1", "SKU-2"], Limit = 3 });
// endregion

// region:config
var tuned = new LikylyClient(new LikylyClientOptions
{
    ApiKey = Environment.GetEnvironmentVariable("LIKYLY_SECRET_KEY")!,
    Timeout = TimeSpan.FromSeconds(5), // per attempt (default 10 s)
    MaxRetries = 3, // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
    UserAgent = "my-shop/1.4", // appended to the SDK's User-Agent
    BaseUrl = Environment.GetEnvironmentVariable("LIKYLY_BASE_URL") ?? LikylyClient.DefaultBaseUrl, // docs:omit
});
// endregion

// region:errors
try
{
    await likyly.Items.GetAsync("does-not-exist");
}
catch (NotFoundException e)
{
    Console.WriteLine($"no such item, request {e.RequestId}");
}
catch (RateLimitException e)
{
    Console.WriteLine($"slow down, retry in {e.RetryAfter}");
}
// endregion

Console.WriteLine($"visitor strategy: {forVisitor.Strategy}");

// region:cleanup
await likyly.Items.DeleteManyAsync(["SKU-1", "SKU-2", "SKU-3"]);
await likyly.Users.DeleteAsync("user_123");
// endregion
Console.WriteLine("quickstart ok");
