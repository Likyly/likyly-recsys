namespace Likyly;

// ---- Items -----------------------------------------------------------------------------------

/// <summary>A catalog item. <paramref name="ItemId"/> is your own identifier - any string (<c>SKU-123</c>, a UUID, <c>gid://shopify/Product/123</c>).</summary>
public sealed record Item(string ItemId, string Title, string? Description, IReadOnlyDictionary<string, object?> Properties);

/// <summary>What you send to create or replace an item. Only <see cref="Title"/> is required.</summary>
public sealed record ItemInput
{
    public required string Title { get; init; }

    /// <summary>Feeds content similarity.</summary>
    public string? Description { get; init; }

    /// <summary>category, brand, price, ... - keys are yours.</summary>
    public IReadOnlyDictionary<string, object?>? Properties { get; init; }
}

/// <summary>One entry of a batch upsert (<c>Items.UpsertManyAsync</c>).</summary>
public sealed record ItemImport
{
    public required string ItemId { get; init; }
    public required string Title { get; init; }
    public string? Description { get; init; }
    public IReadOnlyDictionary<string, object?>? Properties { get; init; }
}

/// <summary>Pagination: <c>Limit</c> + <c>Offset</c>.</summary>
public sealed record ListOptions
{
    /// <summary>Page size (1-1000); null = the API's default.</summary>
    public int? Limit { get; init; }

    public int? Offset { get; init; }
}

/// <summary>One page of the catalog. <paramref name="Total"/> is the whole catalog's size (<c>X-Total-Count</c>), null if not reported.</summary>
public sealed record ItemList(IReadOnlyList<Item> Items, long? Total, int? Limit, int Offset);

// ---- Users -----------------------------------------------------------------------------------

public sealed record User(string UserId, IReadOnlyDictionary<string, object?> Properties);

/// <summary>What you send to create or replace a user profile: country, segment, language, ... - whatever describes your users.</summary>
public sealed record UserInput
{
    public IReadOnlyDictionary<string, object?>? Properties { get; init; }
}

public sealed record UserImport
{
    public required string UserId { get; init; }
    public IReadOnlyDictionary<string, object?>? Properties { get; init; }
}

public sealed record UserList(IReadOnlyList<User> Users, long? Total, int? Limit, int Offset);

/// <summary>One failing entry of a batch call.</summary>
public sealed record BatchError(int Index, string? Id, string Message);

/// <summary>Result of a batch call. A failing entry is reported in <see cref="Errors"/>, it does not throw.</summary>
public sealed record BatchResult(int Received, int Succeeded, int Failed, IReadOnlyList<BatchError> Errors);

// ---- Events ----------------------------------------------------------------------------------

/// <summary>
/// An interaction. <see cref="ItemId"/> and at least one of <see cref="UserId"/> / <see cref="SessionId"/> are required;
/// sending both ties an anonymous session to the user.
/// </summary>
public record EventInput
{
    public required string ItemId { get; init; }
    public string? UserId { get; init; }

    /// <summary>Anonymous visitor / browsing session - no login needed.</summary>
    public string? SessionId { get; init; }

    /// <summary>The RecommendationId of the recommendation that surfaced this item - enables attribution.</summary>
    public string? RecommendationId { get; init; }

    /// <summary>Free-form label of where this happened: <c>homepage</c>, <c>product_page</c>, <c>cart</c>, ...</summary>
    public string? Placement { get; init; }

    public int? Quantity { get; init; }

    /// <summary>Defaults to the server's time.</summary>
    public DateTimeOffset? OccurredAt { get; init; }

    /// <summary>price, currency, orderId, ... - keys are yours, never renamed.</summary>
    public IReadOnlyDictionary<string, object?>? Properties { get; init; }

    /// <summary>Idempotency key, unique per account: a replay records nothing and returns <c>Duplicate = true</c>. Set it on purchases - it makes retries safe.</summary>
    public string? EventId { get; init; }
}

/// <summary>An event of any type, for <c>Events.TrackManyAsync</c>. <see cref="Type"/> is an open string (<c>view</c>, <c>favorite</c>, ...).</summary>
public sealed record TypedEventInput : EventInput
{
    public required string Type { get; init; }
}

/// <summary>Result of one tracked event. <paramref name="Duplicate"/> is true if this EventId was already recorded - nothing was written.</summary>
public sealed record EventResult(string Message, string? EventId, bool Duplicate);

/// <summary>Result of <c>Events.TrackManyAsync</c>. <paramref name="Duplicates"/> = events skipped because their EventId was already recorded.</summary>
public sealed record EventBatchResult(int Received, int Accepted, int Duplicates);

// ---- Recommendations -------------------------------------------------------------------------

/// <summary>Send whatever you know: LIKYLY chooses the best strategy. Every field is optional.</summary>
public sealed record RecommendationRequest
{
    public string? UserId { get; init; }
    public string? SessionId { get; init; }

    /// <summary>The item being looked at (e.g. the product page).</summary>
    public string? ItemId { get; init; }

    /// <summary>Recently viewed items, oldest first.</summary>
    public IReadOnlyList<string>? ViewedItemIds { get; init; }

    /// <summary>Free-form label of where the recommendations will be shown.</summary>
    public string? Placement { get; init; }

    /// <summary>How many items to return (1-100, default 10).</summary>
    public int? Limit { get; init; }

    /// <summary>Diagnostic detail in explanations. Secret key only.</summary>
    public bool? Debug { get; init; }
}

public sealed record SimilarUser(string UserId, IReadOnlyList<string> SharedItemIds);

/// <summary>Why an item was recommended. <see cref="Reason"/> is always present.</summary>
public sealed record Explanation(
    string Reason,
    double? ContentSimilarity,
    double? SemanticSimilarity,
    double? PopularityScore,
    int? InteractionCount,
    string? InteractionLabel,
    double? CollaborativeScore,
    IReadOnlyList<string>? SourceItemIds,
    IReadOnlyList<SimilarUser>? SimilarUsers);

public sealed record RecommendedItem(
    string ItemId,
    double? Score,
    string? Title,
    string? Description,
    IReadOnlyDictionary<string, object?> Properties,
    Explanation? Explanation);

/// <summary>
/// What <c>Recommendations.GetAsync</c> returns. Send <see cref="RecommendationId"/> back on the impression / click /
/// add_to_cart / purchase events for these items. <see cref="Strategy"/> is what LIKYLY used.
/// </summary>
public sealed record RecommendationResponse(string RecommendationId, string Strategy, string? Placement, IReadOnlyList<RecommendedItem> Items);

/// <summary>Options shared by the Advanced Recommendations.</summary>
public record AdvancedOptions
{
    /// <summary>How many items (default 10).</summary>
    public int? Limit { get; init; }

    public string? Placement { get; init; }

    /// <summary>Attach the recommendation to an anonymous session, for attribution.</summary>
    public string? SessionId { get; init; }
}

public sealed record PopularOptions : AdvancedOptions;

public sealed record SimilarOptions : AdvancedOptions
{
    public required string ItemId { get; init; }
}

public sealed record CollaborativeOptions : AdvancedOptions
{
    public required string UserId { get; init; }
}

public sealed record HybridOptions : AdvancedOptions
{
    public required string UserId { get; init; }
    public required string ItemId { get; init; }

    /// <summary>Weight of the collaborative signal against content similarity: 0 = pure content, 1 = pure collaborative (default 0.5).</summary>
    public double? Alpha { get; init; }
}

/// <summary>Give <see cref="ViewedItemIds"/> (an explicit list, oldest first) OR <see cref="UserId"/> (LIKYLY's own history of that user's views) - exactly one.</summary>
public sealed record SessionOptions : AdvancedOptions
{
    public IReadOnlyList<string>? ViewedItemIds { get; init; }
    public string? UserId { get; init; }
}
