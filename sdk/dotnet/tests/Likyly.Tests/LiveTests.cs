using Xunit;

namespace Likyly.Tests;

/// <summary>End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless LIKYLY_TEST_* are set.</summary>
public class LiveTests
{
    private const string Gid = "gid://shopify/Product/123456";

    [Fact]
    public async Task FullWorkflow()
    {
        var url = Environment.GetEnvironmentVariable("LIKYLY_TEST_URL");
        var secret = Environment.GetEnvironmentVariable("LIKYLY_TEST_SECRET_KEY");
        var pub = Environment.GetEnvironmentVariable("LIKYLY_TEST_PUBLIC_KEY");
        if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(secret) || string.IsNullOrEmpty(pub))
        {
            return; // LIKYLY_TEST_* not set: skipped
        }

        var catalog = $"dotnet-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}";
        var server = new LikylyClient(new LikylyClientOptions { ApiKey = secret, BaseUrl = url, Catalog = catalog });
        var browser = new LikylyClient(new LikylyClientOptions { ApiKey = pub, BaseUrl = url, Catalog = catalog });

        var created = await server.Items.UpsertAsync("SKU-123", new ItemInput { Title = "Nike Air Max", Description = "Running shoe", Properties = new Dictionary<string, object?> { ["category"] = "shoes", ["price"] = 129.9 } });
        Assert.Equal("SKU-123", created.ItemId);
        Assert.Equal(129.9, created.Properties["price"]);
        await server.Items.UpsertAsync(Gid, new ItemInput { Title = "Shopify boot", Description = "warm winter boot", Properties = new Dictionary<string, object?> { ["category"] = "boots" } });
        Assert.Equal("Shopify boot", (await server.Items.GetAsync(Gid)).Title);
        var page = await server.Items.ListAsync(new ListOptions { Limit = 1 });
        Assert.Single(page.Items);
        Assert.Equal(2, page.Total);
        Assert.Equal(1, (await server.Items.UpsertManyAsync([new ItemImport { ItemId = "SKU-A", Title = "Adidas", Description = "road running shoe" }])).Succeeded);
        await server.Items.DeleteAsync("SKU-A");
        await Assert.ThrowsAsync<NotFoundException>(() => server.Items.GetAsync("SKU-A"));

        Assert.Equal("premium", (await server.Users.UpsertAsync("user_123", new UserInput { Properties = new Dictionary<string, object?> { ["country"] = "FR", ["segment"] = "premium" } })).Properties["segment"]);
        Assert.NotEmpty((await server.Users.ListAsync(new ListOptions { Limit = 10 })).Users);

        await browser.Events.ViewAsync(new EventInput { ItemId = "SKU-123", UserId = "user_123" });
        await browser.Events.ViewAsync(new EventInput { ItemId = "SKU-123", SessionId = "sess_123" });
        await browser.Events.TrackAsync("favorite", new EventInput { ItemId = "SKU-123", UserId = "user_123" });
        var purchase = new EventInput { ItemId = "SKU-123", UserId = "user_123", EventId = $"purchase_{Environment.TickCount64}", Properties = new Dictionary<string, object?> { ["orderId"] = "O-1" } };
        Assert.False((await browser.Events.PurchaseAsync(purchase)).Duplicate);
        Assert.True((await browser.Events.PurchaseAsync(purchase)).Duplicate);
        Assert.Equal(1, (await browser.Events.TrackManyAsync([new TypedEventInput { Type = "view", ItemId = Gid, UserId = "user_123" }])).Accepted);

        var rec = await browser.Recommendations.GetAsync(new RecommendationRequest { UserId = "user_123", Placement = "homepage", Limit = 2 });
        Assert.StartsWith("rec_", rec.RecommendationId);
        Assert.Equal("homepage", rec.Placement);
        Assert.NotEmpty(rec.Items);
        Assert.Equal("content", (await browser.Recommendations.GetAsync(new RecommendationRequest { ItemId = "SKU-123", Limit = 2 })).Strategy);
        Assert.Equal("content", (await browser.Recommendations.GetAsync(new RecommendationRequest { ItemId = Gid, Limit = 2 })).Strategy);
        await browser.Events.ImpressionAsync(new EventInput { ItemId = rec.Items[0].ItemId, UserId = "user_123", RecommendationId = rec.RecommendationId, Placement = "homepage" });
        Assert.Equal("content", (await browser.Recommendations.SimilarAsync(new SimilarOptions { ItemId = "SKU-123", Limit = 2 })).Strategy);
        Assert.Equal("session", (await browser.Recommendations.SessionAsync(new SessionOptions { ViewedItemIds = ["SKU-123"], Limit = 2 })).Strategy);
        Assert.NotEmpty((await browser.Recommendations.PopularAsync()).RecommendationId);

        await Assert.ThrowsAsync<PermissionDeniedException>(() => browser.Items.UpsertAsync("x", new ItemInput { Title = "t" }));
        await Assert.ThrowsAsync<AuthenticationException>(() => new LikylyClient(new LikylyClientOptions { ApiKey = "nope", BaseUrl = url }).Events.ViewAsync(new EventInput { ItemId = "i", UserId = "u" }));
        var e = await Assert.ThrowsAsync<ValidationException>(() => server.Items.ListAsync(new ListOptions { Limit = 5000 }));
        Assert.StartsWith("req_", e.RequestId);

        await server.Items.DeleteManyAsync(["SKU-123", Gid]);
        await server.Users.DeleteAsync("user_123");
    }
}
