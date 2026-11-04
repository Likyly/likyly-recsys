using System.Text.Json.Nodes;
using Likyly.Internal;

namespace Likyly;

/// <summary>
/// <c>client.Placements</c> - the recommended way to ask for recommendations. A placement ("what to recommend, where, with
/// what fallback and filters") is configured once - with the LIKYLY MCP admin tools or the dashboard, never from this SDK -
/// and called by its slug from here on. <c>client.Recommendations</c> (one strategy or signal set per call, no stored
/// configuration) remains available for advanced use.
/// </summary>
public sealed class PlacementsResource
{
    private readonly HttpCore _http;

    internal PlacementsResource(HttpCore http) => _http = http;

    /// <summary>
    /// Recommendations for one placement: send whatever context you have (<c>ItemId</c>, <c>UserId</c>, <c>SessionId</c>, ...) -
    /// the placement's own configuration decides the strategy, fallback and filters. Send <c>RecommendationId</c> back on the
    /// events that follow to attribute them.
    /// </summary>
    public async Task<PlacementRecommendResponse> RecommendAsync(PlacementRecommendRequest request, CancellationToken ct = default)
    {
        var placement = Support.RequireId(request?.Placement, "placement");
        var body = new JsonObject();
        if (request!.Context is { } context)
        {
            body["context"] = ContextBody(context);
        }

        if (request.Limit is { } limit)
        {
            body["limit"] = Support.Limit(limit);
        }

        // A read in effect: each call mints a fresh RecommendationId server-side, so a replay after a dropped connection is harmless.
        return Wire.PlacementRecommendation((await _http.RequestAsync(HttpMethod.Post, $"/placements/{HttpCore.Encode(placement)}/recommend", null, body, true, ct).ConfigureAwait(false)).Data);
    }

    private static JsonObject ContextBody(PlacementContext c)
    {
        var node = new JsonObject();
        Support.Put(node, "current_item_id", c.ItemId);
        Support.Put(node, "category_id", c.CategoryId);
        Support.Put(node, "collection_id", c.CollectionId);
        Support.Put(node, "user_id", c.UserId);
        Support.Put(node, "anonymous_id", c.AnonymousId);
        Support.Put(node, "session_id", c.SessionId);
        if (c.CartItemIds is not null)
        {
            var ids = new JsonArray();
            foreach (var id in c.CartItemIds)
            {
                ids.Add(Support.RequireId(id, "cartItemIds[]"));
            }

            node["cart_item_ids"] = ids;
        }

        Support.Put(node, "locale", c.Locale);
        if (c.CustomContext is not null)
        {
            node["custom_context"] = Json.FromObject(c.CustomContext) ?? new JsonObject();
        }

        return node;
    }
}
