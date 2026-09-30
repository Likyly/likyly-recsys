using System.Text.Json.Nodes;
using Likyly.Internal;

namespace Likyly;

/// <summary><c>client.Items</c> - your catalog. Needs the <b>secret</b> API key.</summary>
public sealed class ItemsResource
{
    private readonly HttpCore _http;

    internal ItemsResource(HttpCore http) => _http = http;

    /// <summary>One item by your own id.</summary>
    public async Task<Item> GetAsync(string itemId, CancellationToken ct = default) =>
        Wire.Item((await _http.RequestAsync(HttpMethod.Get, $"/items/{HttpCore.Encode(Support.RequireId(itemId, "itemId"))}", null, null, true, ct).ConfigureAwait(false)).Data);

    /// <summary>One page of the catalog. Pagination is <c>Limit</c> + <c>Offset</c>; <c>Total</c> is the whole catalog's size.</summary>
    public async Task<ItemList> ListAsync(ListOptions? options = null, CancellationToken ct = default)
    {
        var query = new List<KeyValuePair<string, string>>();
        if (options?.Limit is { } limit)
        {
            query.Add(new("limit", limit.ToString()));
        }

        if (options?.Offset is { } offset)
        {
            query.Add(new("offset", offset.ToString()));
        }

        var r = await _http.RequestAsync(HttpMethod.Get, "/items", query, null, true, ct).ConfigureAwait(false);
        var items = (r.Data as JsonArray ?? []).Select(Wire.Item).ToList();
        return new ItemList(items, r.Headers.TryGetValue("x-total-count", out var t) ? long.Parse(t) : null, options?.Limit, options?.Offset ?? 0);
    }

    /// <summary>Creates the item, or replaces it if it exists - idempotent. Fields you leave out are cleared.</summary>
    public async Task<Item> UpsertAsync(string itemId, ItemInput item, CancellationToken ct = default) =>
        Wire.Item((await _http.RequestAsync(HttpMethod.Put, $"/items/{HttpCore.Encode(Support.RequireId(itemId, "itemId"))}", null, Body(item.Title, item.Description, item.Properties), true, ct).ConfigureAwait(false)).Data);

    /// <summary>Removes the item from the catalog. Events already recorded for it are kept.</summary>
    public async Task DeleteAsync(string itemId, CancellationToken ct = default) =>
        await _http.RequestAsync(HttpMethod.Delete, $"/items/{HttpCore.Encode(Support.RequireId(itemId, "itemId"))}", null, null, true, ct).ConfigureAwait(false);

    /// <summary>Batch upsert (1-1000). A failing entry is reported in <c>Errors</c>, it does not throw.</summary>
    public async Task<BatchResult> UpsertManyAsync(IReadOnlyList<ItemImport> items, CancellationToken ct = default)
    {
        if (items is null || items.Count == 0)
        {
            throw new ValidationException("items must be a non-empty list");
        }

        var array = new JsonArray();
        foreach (var i in items)
        {
            var entry = new JsonObject { ["item_id"] = Support.RequireId(i.ItemId, "itemId") };
            foreach (var (key, value) in Body(i.Title, i.Description, i.Properties))
            {
                entry[key] = value?.DeepClone();
            }

            array.Add(entry);
        }

        return Wire.Batch((await _http.RequestAsync(HttpMethod.Post, "/items/import", null, new JsonObject { ["items"] = array }, true, ct).ConfigureAwait(false)).Data);
    }

    /// <summary>Alias of <see cref="UpsertManyAsync"/>: the API's <c>POST /items/import</c> is a JSON batch upsert.</summary>
    public Task<BatchResult> ImportAsync(IReadOnlyList<ItemImport> items, CancellationToken ct = default) => UpsertManyAsync(items, ct);

    /// <summary>Batch delete (1-1000). Ids that don't exist are reported in <c>Errors</c>.</summary>
    public async Task<BatchResult> DeleteManyAsync(IReadOnlyList<string> itemIds, CancellationToken ct = default)
    {
        if (itemIds is null || itemIds.Count == 0)
        {
            throw new ValidationException("itemIds must be a non-empty list");
        }

        var ids = new JsonArray();
        foreach (var id in itemIds)
        {
            ids.Add(Support.RequireId(id, "itemId"));
        }

        return Wire.Batch((await _http.RequestAsync(HttpMethod.Post, "/items/delete", null, new JsonObject { ["item_ids"] = ids }, true, ct).ConfigureAwait(false)).Data);
    }

    private static JsonObject Body(string? title, string? description, IReadOnlyDictionary<string, object?>? properties)
    {
        if (string.IsNullOrEmpty(title))
        {
            throw new ValidationException("an item needs a non-empty title");
        }

        var node = new JsonObject { ["title"] = title };
        if (description is not null)
        {
            node["description"] = description;
        }

        Support.PutProperties(node, properties);
        return node;
    }
}
