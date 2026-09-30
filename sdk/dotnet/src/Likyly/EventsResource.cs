using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Likyly.Internal;

namespace Likyly;

/// <summary>
/// <c>client.Events</c> - what your visitors do. Works with the <b>public</b> key from a browser-facing service, or the
/// secret key. <see cref="TrackAsync"/> is the one mechanism; <see cref="ViewAsync"/>, <see cref="ClickAsync"/>, ... are
/// shortcuts that call it with the matching event type. Event types are open strings (<c>favorite</c>, <c>share</c>, ...):
/// the six helpers are conveniences, not a closed list.
/// <para>A failed call is only retried automatically when it carries an <c>EventId</c> (then a replay is harmless);
/// without one, an ambiguous failure is thrown rather than risking a duplicate.</para>
/// </summary>
public sealed partial class EventsResource
{
    private readonly HttpCore _http;

    internal EventsResource(HttpCore http) => _http = http;

    [GeneratedRegex("^[A-Za-z0-9_-]{1,64}$")]
    private static partial Regex TypePattern();

    /// <summary>Records one event of any type. Needs <c>ItemId</c> and a <c>UserId</c> and/or a <c>SessionId</c>.</summary>
    public async Task<EventResult> TrackAsync(string type, EventInput evt, CancellationToken ct = default) =>
        Wire.EventResult((await _http.RequestAsync(HttpMethod.Post, $"/events/{HttpCore.Encode(CheckType(type))}", null, ToWire(evt), evt.EventId is not null, ct).ConfigureAwait(false)).Data);

    /// <summary>Up to 1000 events in one call, each with its own <c>Type</c>. All-or-nothing validation.</summary>
    public async Task<EventBatchResult> TrackManyAsync(IReadOnlyList<TypedEventInput> events, CancellationToken ct = default)
    {
        if (events is null || events.Count == 0)
        {
            throw new ValidationException("events must be a non-empty list");
        }

        var array = new JsonArray();
        foreach (var e in events)
        {
            var entry = new JsonObject { ["event_type"] = CheckType(e.Type) };
            foreach (var (key, value) in ToWire(e))
            {
                entry[key] = value?.DeepClone();
            }

            array.Add(entry);
        }

        var allIdempotent = events.All(e => e.EventId is not null);
        return Wire.EventBatch((await _http.RequestAsync(HttpMethod.Post, "/events/batch", null, new JsonObject { ["events"] = array }, allIdempotent, ct).ConfigureAwait(false)).Data);
    }

    /// <summary>The item was shown to the visitor (send the <c>RecommendationId</c> it came with).</summary>
    public Task<EventResult> ImpressionAsync(EventInput evt, CancellationToken ct = default) => TrackAsync("impression", evt, ct);

    /// <summary>The visitor looked at the item (a product page, an article).</summary>
    public Task<EventResult> ViewAsync(EventInput evt, CancellationToken ct = default) => TrackAsync("view", evt, ct);

    /// <summary>The visitor clicked the item.</summary>
    public Task<EventResult> ClickAsync(EventInput evt, CancellationToken ct = default) => TrackAsync("click", evt, ct);

    public Task<EventResult> AddToCartAsync(EventInput evt, CancellationToken ct = default) => TrackAsync("add_to_cart", evt, ct);

    public Task<EventResult> RemoveFromCartAsync(EventInput evt, CancellationToken ct = default) => TrackAsync("remove_from_cart", evt, ct);

    /// <summary>Set <c>EventId</c> (e.g. <c>purchase_{orderId}_{itemId}</c>) so a retry can never count the purchase twice.</summary>
    public Task<EventResult> PurchaseAsync(EventInput evt, CancellationToken ct = default) => TrackAsync("purchase", evt, ct);

    private static string CheckType(string? type) =>
        type is not null && TypePattern().IsMatch(type)
            ? type
            : throw new ValidationException("event type must be 1-64 characters of letters, digits, \"_\" or \"-\" (e.g. \"view\", \"add_to_cart\", \"favorite\")");

    private static JsonObject ToWire(EventInput e)
    {
        Support.RequireId(e.ItemId, "itemId");
        if (e.UserId is null && e.SessionId is null)
        {
            throw new ValidationException("an event needs a UserId or a SessionId (or both)");
        }

        if (e.UserId is not null)
        {
            Support.RequireId(e.UserId, "userId");
        }

        if (e.SessionId is not null)
        {
            Support.RequireId(e.SessionId, "sessionId");
        }

        var node = new JsonObject();
        Support.Put(node, "event_id", e.EventId);
        Support.Put(node, "user_id", e.UserId);
        Support.Put(node, "session_id", e.SessionId);
        node["item_id"] = e.ItemId;
        Support.Put(node, "recommendation_id", e.RecommendationId);
        Support.Put(node, "placement", e.Placement);
        Support.Put(node, "quantity", e.Quantity);
        Support.Put(node, "occurred_at", e.OccurredAt?.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'", System.Globalization.CultureInfo.InvariantCulture));
        Support.PutProperties(node, e.Properties);
        return node;
    }
}
