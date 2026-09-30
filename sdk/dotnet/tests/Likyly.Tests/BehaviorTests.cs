using System.Net.Http;
using System.Text.Json.Nodes;
using Xunit;

namespace Likyly.Tests;

public class BehaviorTests
{
    private const string Rec = """{"recommendation_id":"rec_1","strategy":"popular","items":[]}""";
    private const string Evt = """{"message":"ok","event_id":null,"duplicate":false}""";
    private const string ItemJson = """{"item_id":"a","title":"t"}""";

    private static EventInput E(string item = "i", string? user = "u", string? eventId = null) => new() { ItemId = item, UserId = user, EventId = eventId };

    // ---- configuration

    [Fact]
    public void RequiresAnApiKey()
    {
        Assert.Throws<ValidationException>(() => new LikylyClient("  "));
        Assert.Throws<ValidationException>(() => new LikylyClient(new LikylyClientOptions { ApiKey = "" }));
    }

    [Fact]
    public async Task DefaultsToTheProductionApiAndAppendsAUserAgent()
    {
        var handler = new FakeHandler(new Step(Body: "[]"));
        var client = new LikylyClient(new LikylyClientOptions { ApiKey = "k", UserAgent = "my-shop/1.4" }, new HttpClient(handler));
        await client.Items.ListAsync();
        Assert.Equal("api.likyly.com", handler.Calls[0].Uri.Host);
        Assert.Matches(@"^likyly-dotnet/\d+\.\d+\.\d+ my-shop/1\.4$", handler.Calls[0].Headers["User-Agent"]);
    }

    // ---- validation before sending

    [Fact]
    public async Task AnEventNeedsAnItemAndAUserOrASession()
    {
        var (client, handler, _) = Harness.Make(2, new Step(Body: Evt));
        await Assert.ThrowsAsync<ValidationException>(() => client.Events.ViewAsync(E(user: null)));
        await Assert.ThrowsAsync<ValidationException>(() => client.Events.ViewAsync(E(item: "")));
        Assert.Empty(handler.Calls);
    }

    [Fact]
    public async Task EventTypesAreOpenButUrlSafe()
    {
        var (client, _, _) = Harness.Make(2, new Step(Body: Evt));
        await client.Events.TrackAsync("favorite", E());
        await Assert.ThrowsAsync<ValidationException>(() => client.Events.TrackAsync("bad type!", E()));
    }

    [Fact]
    public async Task AdvancedEndpointsRefuseAnIdContainingASlash()
    {
        var (client, handler, _) = Harness.Make(2, new Step(Body: Rec));
        var e = await Assert.ThrowsAsync<ValidationException>(() => client.Recommendations.SimilarAsync(new SimilarOptions { ItemId = "gid://shopify/Product/1" }));
        Assert.Contains("Recommendations.GetAsync()", e.Message);
        await client.Recommendations.GetAsync(new RecommendationRequest { ItemId = "gid://shopify/Product/1" });
        Assert.Single(handler.Calls);
    }

    [Fact]
    public async Task SessionNeedsExactlyOneOfListOrUser()
    {
        var (client, _, _) = Harness.Make(2, new Step(Body: Rec));
        await Assert.ThrowsAsync<ValidationException>(() => client.Recommendations.SessionAsync(new SessionOptions()));
        await Assert.ThrowsAsync<ValidationException>(() => client.Recommendations.SessionAsync(new SessionOptions { ViewedItemIds = ["a"], UserId = "u" }));
        await Assert.ThrowsAsync<ValidationException>(() => client.Recommendations.SessionAsync(new SessionOptions { ViewedItemIds = [] }));
    }

    [Fact]
    public async Task BatchesMustNotBeEmpty()
    {
        var (client, _, _) = Harness.Make(2, new Step(Body: Evt));
        await Assert.ThrowsAsync<ValidationException>(() => client.Items.UpsertManyAsync([]));
        await Assert.ThrowsAsync<ValidationException>(() => client.Events.TrackManyAsync([]));
    }

    [Fact]
    public async Task ADateTimeOffsetIsSerializedToIso8601Utc()
    {
        var (client, handler, _) = Harness.Make(2, new Step(Body: Evt));
        await client.Events.ViewAsync(E() with { OccurredAt = new DateTimeOffset(2026, 9, 24, 12, 30, 0, TimeSpan.FromHours(2)) });
        Assert.Equal("2026-09-24T10:30:00Z", JsonNode.Parse(handler.Calls[0].Body!)!["occurred_at"]!.GetValue<string>());
    }

    [Fact]
    public async Task PropertiesKeysAreNeverRenamedAndEmptyPropertiesStayAnObject()
    {
        var (client, handler, _) = Harness.Make(2, new Step(Body: Evt), new Step(Body: ItemJson));
        await client.Events.PurchaseAsync(E() with { Properties = new Dictionary<string, object?> { ["orderId"] = "O-1", ["nested"] = new Dictionary<string, object?> { ["snakeCase_and_camelCase"] = 1 } } });
        var props = JsonNode.Parse(handler.Calls[0].Body!)!["properties"]!;
        Assert.Equal("O-1", props["orderId"]!.GetValue<string>());
        Assert.Equal(1, props["nested"]!["snakeCase_and_camelCase"]!.GetValue<int>());
        await client.Items.UpsertAsync("a", new ItemInput { Title = "t", Properties = new Dictionary<string, object?>() });
        Assert.Contains("\"properties\":{}", handler.Calls[1].Body);
    }

    // ---- errors

    [Fact]
    public async Task TheHierarchyIsUsable()
    {
        var (client, _, _) = Harness.Make(0, new Step(401, """{"detail":"nope","request_id":"req_x"}"""));
        var e = await Assert.ThrowsAsync<AuthenticationException>(() => client.Items.GetAsync("a"));
        Assert.IsAssignableFrom<ApiException>(e);
        Assert.IsAssignableFrom<LikylyException>(e);
        Assert.Equal("req_x", e.RequestId);
    }

    [Fact]
    public async Task A422ListsTheOffendingFields()
    {
        var (client, _, _) = Harness.Make(0, new Step(422, """{"detail":[{"loc":["body","title"],"msg":"Field required"}]}"""));
        var e = await Assert.ThrowsAsync<ValidationException>(() => client.Items.GetAsync("a"));
        Assert.Contains("Field required", e.Message);
    }

    [Fact]
    public async Task ANonJsonErrorBodyStillBecomesAnApiException()
    {
        var (client, _, _) = Harness.Make(0, new Step(502, "<html>Bad gateway</html>"));
        var e = await Assert.ThrowsAsync<ApiException>(() => client.Items.GetAsync("a"));
        Assert.Equal(502, e.StatusCode);
    }

    [Fact]
    public async Task NetworkFailureAndTimeoutAreDistinguished()
    {
        var (net, _, _) = Harness.Make(0, new Step(Failure: new HttpRequestException("reset")));
        await Assert.ThrowsAsync<NetworkException>(() => net.Items.GetAsync("a"));

        var (slow, _, _) = Harness.Make(new LikylyClientOptions { ApiKey = "k", BaseUrl = "https://x.test", MaxRetries = 0, Timeout = TimeSpan.FromMilliseconds(20) },
            new Step(Failure: new TaskCanceledException()));
        await Assert.ThrowsAsync<RequestTimeoutException>(() => slow.Items.GetAsync("a"));
    }

    [Fact]
    public async Task TheCallersCancellationTokenSurfacesAsOperationCanceled()
    {
        var (client, _, _) = Harness.Make(0, new Step(Failure: new TaskCanceledException()));
        using var cts = new CancellationTokenSource();
        cts.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => client.Items.GetAsync("a", cts.Token));
    }

    // ---- retries

    [Fact]
    public async Task A429IsRetriedForEveryRequestHonoringRetryAfter()
    {
        var (client, handler, sleeps) = Harness.Make(2, new Step(429, """{"detail":"slow"}""", new() { ["Retry-After"] = "3" }), new Step(Body: Evt));
        Assert.False((await client.Events.ViewAsync(E())).Duplicate); // no eventId: still safe - 429 never reached the app
        Assert.Equal(2, handler.Calls.Count);
        Assert.Equal([TimeSpan.FromSeconds(3)], sleeps);
    }

    [Fact]
    public async Task ARetryAfterBeyondAMinuteIsNotWaitedFor()
    {
        var (client, handler, _) = Harness.Make(2, new Step(429, """{"detail":"x"}""", new() { ["Retry-After"] = "600" }));
        var e = await Assert.ThrowsAsync<RateLimitException>(() => client.Items.GetAsync("a"));
        Assert.Equal(600.0, e.RetryAfter);
        Assert.Single(handler.Calls);
    }

    [Fact]
    public async Task ExponentialBackoffWithJitterThenGivesUp()
    {
        var (client, handler, sleeps) = Harness.Make(3, new Step(503, """{"detail":"down"}"""));
        await Assert.ThrowsAsync<ApiException>(() => client.Items.GetAsync("a"));
        Assert.Equal(4, handler.Calls.Count);
        Assert.Equal([TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(1000), TimeSpan.FromMilliseconds(2000)], sleeps);
    }

    [Theory]
    [InlineData(502)]
    [InlineData(503)]
    [InlineData(504)]
    public async Task IdempotentCallsRetryOnGatewayErrors(int status)
    {
        var (client, handler, _) = Harness.Make(2, new Step(status, """{"detail":"x"}"""), new Step(Body: ItemJson));
        await client.Items.UpsertAsync("a", new ItemInput { Title = "t" });
        Assert.Equal(2, handler.Calls.Count);
    }

    [Fact]
    public async Task AnEventWithoutEventIdIsNeverRetriedOnAnAmbiguousFailure()
    {
        foreach (var first in new[] { new Step(503, """{"detail":"x"}"""), new Step(Failure: new HttpRequestException("reset")), new Step(Failure: new TaskCanceledException()) })
        {
            var (client, handler, _) = Harness.Make(2, first, new Step(Body: Evt));
            await Assert.ThrowsAnyAsync<LikylyException>(() => client.Events.PurchaseAsync(E()));
            Assert.Single(handler.Calls);
        }
    }

    [Fact]
    public async Task AnEventWithEventIdIsRetriedTheReplayIsHarmless()
    {
        var (client, handler, _) = Harness.Make(2, new Step(503, """{"detail":"x"}"""), new Step(Body: """{"message":"dup","event_id":"e1","duplicate":true}"""));
        Assert.True((await client.Events.PurchaseAsync(E(eventId: "e1"))).Duplicate);
        Assert.Equal(2, handler.Calls.Count);
    }

    [Fact]
    public async Task TrackManyIsRetriedOnlyIfEveryEventHasAnEventId()
    {
        const string batch = """{"received":2,"accepted":2,"duplicates":0}""";
        TypedEventInput T(string item, string? eventId) => new() { Type = "view", ItemId = item, UserId = "u", EventId = eventId };
        var (a, ha, _) = Harness.Make(2, new Step(503, """{"detail":"x"}"""), new Step(Body: batch));
        await Assert.ThrowsAsync<ApiException>(() => a.Events.TrackManyAsync([T("1", null), T("2", "e")]));
        Assert.Single(ha.Calls);
        var (b, hb, _) = Harness.Make(2, new Step(503, """{"detail":"x"}"""), new Step(Body: batch));
        await b.Events.TrackManyAsync([T("1", "a"), T("2", "b")]);
        Assert.Equal(2, hb.Calls.Count);
    }

    [Theory]
    [InlineData(400)]
    [InlineData(401)]
    [InlineData(403)]
    [InlineData(404)]
    [InlineData(422)]
    public async Task ClientErrorsAreNeverRetried(int status)
    {
        var (client, handler, _) = Harness.Make(2, new Step(status, """{"detail":"x"}"""), new Step(Body: "{}"));
        await Assert.ThrowsAnyAsync<LikylyException>(() => client.Items.GetAsync("a"));
        Assert.Single(handler.Calls);
    }

    // ---- lists

    [Fact]
    public async Task ListsReportTheTotalAndOnlySendWhatWasAskedFor()
    {
        var (client, handler, _) = Harness.Make(2, new Step(Body: $"[{ItemJson}]", Headers: new() { ["X-Total-Count"] = "42" }), new Step(Body: "[]"));
        var page = await client.Items.ListAsync(new ListOptions { Limit = 1 });
        Assert.Equal(42, page.Total);
        Assert.Equal(1, page.Limit);
        await client.Items.ListAsync();
        Assert.DoesNotContain("?", handler.Calls[1].RawPathAndQuery);
    }

    [Fact]
    public async Task ImportIsAnAliasOfUpsertMany()
    {
        var (client, handler, _) = Harness.Make(2, new Step(Body: """{"received":1,"succeeded":1,"failed":0}"""));
        await client.Items.ImportAsync([new ItemImport { ItemId = "a", Title = "A" }]);
        Assert.Equal("/items/import", handler.Calls[0].RawPathAndQuery);
    }
}
