using System.Text.Json.Nodes;

namespace Likyly.Internal;

/// <summary>JSON (snake_case) responses to the public types. <c>properties</c> objects are kept exactly as the API returned them.</summary>
internal static class Wire
{
    public static Item Item(JsonNode? n) => new(Json.Str(n, "item_id")!, Json.Str(n, "title")!, Json.Str(n, "description"), Json.Properties(n?["properties"]));

    public static User User(JsonNode? n) => new(Json.Str(n, "user_id")!, Json.Properties(n?["properties"]));

    public static BatchResult Batch(JsonNode? n) => new(
        Json.Int(n, "received")!.Value, Json.Int(n, "succeeded")!.Value, Json.Int(n, "failed")!.Value,
        (n?["errors"] as JsonArray ?? []).Select(e => new BatchError(Json.Int(e, "index")!.Value, Json.Str(e, "id"), Json.Str(e, "message")!)).ToList());

    public static EventResult EventResult(JsonNode? n) => new(Json.Str(n, "message")!, Json.Str(n, "event_id"), n?["duplicate"] is JsonValue d && d.GetValue<bool>());

    public static EventBatchResult EventBatch(JsonNode? n) => new(Json.Int(n, "received")!.Value, Json.Int(n, "accepted")!.Value, Json.Int(n, "duplicates")!.Value);

    private static Explanation Explanation(JsonNode n) => new(
        Json.Str(n, "reason")!, Json.Dbl(n, "content_similarity"), Json.Dbl(n, "semantic_similarity"), Json.Dbl(n, "popularity_score"),
        Json.Int(n, "interaction_count"), Json.Str(n, "interaction_label"), Json.Dbl(n, "collaborative_score"), Json.Strings(n, "source_item_ids"),
        n["similar_users"] is JsonArray users
            ? users.Select(u => new SimilarUser(Json.Str(u, "user_id")!, Json.Strings(u, "shared_item_ids") ?? [])).ToList()
            : null);

    public static RecommendationResponse Recommendation(JsonNode? n) => new(
        Json.Str(n, "recommendation_id")!, Json.Str(n, "strategy")!, Json.Str(n, "placement"),
        (n?["items"] as JsonArray ?? []).Select(i => new RecommendedItem(
            Json.Str(i, "item_id")!, Json.Dbl(i, "score"), Json.Str(i, "title"), Json.Str(i, "description"), Json.Properties(i?["properties"]),
            i?["explanation"] is JsonObject ex ? Explanation(ex) : null)).ToList());
}
