using System.Text.Json.Nodes;
using Likyly.Internal;

namespace Likyly;

/// <summary>
/// <c>client.Users</c> - optional user profiles. Needs the <b>secret</b> API key (profiles are personal data).
/// You don't have to create a user before sending events for them.
/// </summary>
public sealed class UsersResource
{
    private readonly HttpCore _http;

    internal UsersResource(HttpCore http) => _http = http;

    public async Task<User> GetAsync(string userId, CancellationToken ct = default) =>
        Wire.User((await _http.RequestAsync(HttpMethod.Get, $"/users/{HttpCore.Encode(Support.RequireId(userId, "userId"))}", null, null, true, ct).ConfigureAwait(false)).Data);

    /// <summary>One page of users. Without <c>Limit</c> the API returns every user.</summary>
    public async Task<UserList> ListAsync(ListOptions? options = null, CancellationToken ct = default)
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

        var r = await _http.RequestAsync(HttpMethod.Get, "/users", query, null, true, ct).ConfigureAwait(false);
        var users = (r.Data as JsonArray ?? []).Select(Wire.User).ToList();
        return new UserList(users, r.Headers.TryGetValue("x-total-count", out var t) ? long.Parse(t) : null, options?.Limit, options?.Offset ?? 0);
    }

    /// <summary>Creates or replaces the profile (idempotent). <c>Properties</c> is free-form: country, segment, language, ...</summary>
    public async Task<User> UpsertAsync(string userId, UserInput? user = null, CancellationToken ct = default)
    {
        var body = new JsonObject();
        Support.PutProperties(body, user?.Properties);
        return Wire.User((await _http.RequestAsync(HttpMethod.Put, $"/users/{HttpCore.Encode(Support.RequireId(userId, "userId"))}", null, body, true, ct).ConfigureAwait(false)).Data);
    }

    /// <summary>Erases the user: the profile <b>and every event recorded for them</b>.</summary>
    public async Task DeleteAsync(string userId, CancellationToken ct = default) =>
        await _http.RequestAsync(HttpMethod.Delete, $"/users/{HttpCore.Encode(Support.RequireId(userId, "userId"))}", null, null, true, ct).ConfigureAwait(false);

    /// <summary>Batch upsert (1-1000).</summary>
    public async Task<BatchResult> ImportAsync(IReadOnlyList<UserImport> users, CancellationToken ct = default)
    {
        if (users is null || users.Count == 0)
        {
            throw new ValidationException("users must be a non-empty list");
        }

        var array = new JsonArray();
        foreach (var u in users)
        {
            var entry = new JsonObject { ["user_id"] = Support.RequireId(u.UserId, "userId") };
            Support.PutProperties(entry, u.Properties);
            array.Add(entry);
        }

        return Wire.Batch((await _http.RequestAsync(HttpMethod.Post, "/users/import", null, new JsonObject { ["users"] = array }, true, ct).ConfigureAwait(false)).Data);
    }
}
