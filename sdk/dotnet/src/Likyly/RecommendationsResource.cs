using Likyly.Internal;

namespace Likyly;

/// <summary>
/// <c>client.Recommendations</c>. Use <see cref="GetAsync"/>: send what you know and LIKYLY picks the best strategy.
/// The other methods are the <i>Advanced Recommendations</i> - one strategy at a time.
/// </summary>
public sealed class RecommendationsResource
{
    private readonly HttpCore _http;

    internal RecommendationsResource(HttpCore http) => _http = http;

    /// <summary>
    /// Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at all (then you get what
    /// is popular). <c>Strategy</c> in the response says what LIKYLY used; send <c>RecommendationId</c> back on the events that
    /// follow. <c>Limit</c> is 1-100 (default 10).
    /// </summary>
    public async Task<RecommendationResponse> GetAsync(RecommendationRequest? request = null, CancellationToken ct = default)
    {
        request ??= new RecommendationRequest();
        var body = new System.Text.Json.Nodes.JsonObject();
        if (request.UserId is not null)
        {
            body["user_id"] = Support.RequireId(request.UserId, "userId");
        }

        if (request.SessionId is not null)
        {
            body["session_id"] = Support.RequireId(request.SessionId, "sessionId");
        }

        if (request.ItemId is not null)
        {
            body["item_id"] = Support.RequireId(request.ItemId, "itemId");
        }

        if (request.ViewedItemIds is not null)
        {
            var ids = new System.Text.Json.Nodes.JsonArray();
            foreach (var id in request.ViewedItemIds)
            {
                ids.Add(Support.RequireId(id, "viewedItemIds[]"));
            }

            body["viewed_item_ids"] = ids;
        }

        Support.Put(body, "placement", request.Placement);
        Support.Put(body, "count", request.Limit);
        if (request.Debug == true)
        {
            body["debug"] = true;
        }

        return Wire.Recommendation((await _http.RequestAsync(HttpMethod.Post, "/getRec", null, body, true, ct).ConfigureAwait(false)).Data);
    }

    // ---- Advanced Recommendations: ids travel in the URL path here, so they cannot contain "/" ----

    /// <summary>The most popular items - the fallback for a visitor with no history at all.</summary>
    public Task<RecommendationResponse> PopularAsync(PopularOptions? options = null, CancellationToken ct = default) =>
        AdvancedAsync($"/getRec/popular/{Support.Limit(options?.Limit)}", options, [], ct);

    /// <summary>Items similar to one item (content similarity). No user needed.</summary>
    public Task<RecommendationResponse> SimilarAsync(SimilarOptions options, CancellationToken ct = default) =>
        AdvancedAsync($"/getRec/content/{HttpCore.Encode(Support.RequirePathSafeId(options.ItemId, "itemId"))}/{Support.Limit(options.Limit)}", options, [], ct);

    /// <summary>What users with similar histories liked. Needs a trained model.</summary>
    public Task<RecommendationResponse> CollaborativeAsync(CollaborativeOptions options, CancellationToken ct = default) =>
        AdvancedAsync($"/getRec/collaborative/{HttpCore.Encode(Support.RequirePathSafeId(options.UserId, "userId"))}/{Support.Limit(options.Limit)}", options, [], ct);

    /// <summary>Similar items, personalized for a user. <c>Alpha</c> (0-1) weighs the collaborative signal against content similarity.</summary>
    public Task<RecommendationResponse> HybridAsync(HybridOptions options, CancellationToken ct = default)
    {
        var path = $"/getRec/hybrid/{HttpCore.Encode(Support.RequirePathSafeId(options.UserId, "userId"))}/{HttpCore.Encode(Support.RequirePathSafeId(options.ItemId, "itemId"))}/{Support.Limit(options.Limit)}";
        var extra = new List<KeyValuePair<string, string>>();
        if (options.Alpha is { } alpha)
        {
            extra.Add(new("alpha", alpha.ToString(System.Globalization.CultureInfo.InvariantCulture)));
        }

        return AdvancedAsync(path, options, extra, ct);
    }

    /// <summary>
    /// Recency-weighted recommendations from what was viewed: pass <c>ViewedItemIds</c> (an explicit list, oldest first) <b>or</b>
    /// <c>UserId</c> (LIKYLY's own history of that user's views) - exactly one.
    /// </summary>
    public Task<RecommendationResponse> SessionAsync(SessionOptions options, CancellationToken ct = default)
    {
        var hasList = options.ViewedItemIds is not null;
        var hasUser = options.UserId is not null;
        if (hasList == hasUser)
        {
            throw new ValidationException("SessionAsync needs either ViewedItemIds or UserId (exactly one)");
        }

        if (hasUser)
        {
            return AdvancedAsync($"/getRec/sessionForUser/{HttpCore.Encode(Support.RequirePathSafeId(options.UserId, "userId"))}/{Support.Limit(options.Limit)}", options, [], ct);
        }

        var ids = options.ViewedItemIds!.Select(id => Support.RequireId(id, "viewedItemIds[]")).ToList();
        if (ids.Count == 0)
        {
            throw new ValidationException("ViewedItemIds must contain at least one item id");
        }

        if (ids.Any(id => id.Contains(',')))
        {
            throw new ValidationException("an item id containing \",\" cannot be sent in this endpoint's comma-separated list - use Recommendations.GetAsync()");
        }

        List<KeyValuePair<string, string>> extra = [new("viewed_item_ids", string.Join(",", ids)), new("count", Support.Limit(options.Limit).ToString())];
        return AdvancedAsync("/getRec/session", options, extra, ct);
    }

    private async Task<RecommendationResponse> AdvancedAsync(string path, AdvancedOptions? options, List<KeyValuePair<string, string>> extra, CancellationToken ct)
    {
        // response_format=object: always the same {recommendation_id, strategy, items} envelope
        var query = new List<KeyValuePair<string, string>> { new("response_format", "object") };
        if (options?.Placement is { } placement)
        {
            query.Add(new("placement", placement));
        }

        if (options?.SessionId is { } sessionId)
        {
            query.Add(new("session_id", sessionId));
        }

        query.AddRange(extra);
        return Wire.Recommendation((await _http.RequestAsync(HttpMethod.Get, path, query, null, true, ct).ConfigureAwait(false)).Data);
    }
}
