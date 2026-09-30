using System.Text.Json;
using System.Text.Json.Nodes;

namespace Likyly.Internal;

/// <summary>JSON conversion helpers.</summary>
internal static class Json
{
    /// <summary>A JSON node as plain .NET values (Dictionary / List / string / long / double / bool / null).</summary>
    public static object? ToObject(JsonNode? node) => node switch
    {
        null => null,
        JsonObject o => o.ToDictionary(kv => kv.Key, kv => ToObject(kv.Value)),
        JsonArray a => a.Select(ToObject).ToList(),
        JsonValue v => ToScalar(v),
        _ => null,
    };

    private static object? ToScalar(JsonValue v)
    {
        var e = v.GetValue<JsonElement>();
        return e.ValueKind switch
        {
            JsonValueKind.String => e.GetString(),
            JsonValueKind.Number => e.TryGetInt64(out var l) ? l : e.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => null,
        };
    }

    public static IReadOnlyDictionary<string, object?> Properties(JsonNode? node) =>
        node is JsonObject o ? (Dictionary<string, object?>)ToObject(o)! : new Dictionary<string, object?>();

    public static string? Str(JsonNode? n, string key) => n?[key] is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    public static double? Dbl(JsonNode? n, string key) => n?[key] is JsonValue v ? v.GetValue<double>() : null;

    public static int? Int(JsonNode? n, string key) => n?[key] is JsonValue v ? v.GetValue<int>() : null;

    public static List<string>? Strings(JsonNode? n, string key) => n?[key] is JsonArray a ? a.Select(x => x!.GetValue<string>()).ToList() : null;

    /// <summary>Copies a properties dictionary into JSON verbatim (its keys are the developer's).</summary>
    public static JsonNode? FromObject(object? value) => value is null ? null : JsonSerializer.SerializeToNode(value);
}

/// <summary>Validation shared by the resources.</summary>
internal static class Support
{
    /// <summary>Ids are opaque, non-empty strings - never coerced to numbers.</summary>
    public static string RequireId(string? value, string name) =>
        string.IsNullOrWhiteSpace(value) ? throw new ValidationException($"{name} must be a non-empty string (your own identifier, e.g. \"SKU-123\")") : value;

    /// <summary>Advanced recommendation endpoints carry ids in the URL path, where '/' cannot be used.</summary>
    public static string RequirePathSafeId(string? value, string name)
    {
        var id = RequireId(value, name);
        return id.Contains('/')
            ? throw new ValidationException($"{name} \"{id}\" contains \"/\", which this endpoint cannot carry in its URL path - use Recommendations.GetAsync(), which takes ids in the request body")
            : id;
    }

    public static int Limit(int? limit)
    {
        var value = limit ?? 10;
        return value < 1 ? throw new ValidationException("limit must be a positive integer") : value;
    }

    public static void Put(JsonObject node, string key, JsonNode? value)
    {
        if (value is not null)
        {
            node[key] = value;
        }
    }

    public static void PutProperties(JsonObject node, IReadOnlyDictionary<string, object?>? properties)
    {
        if (properties is not null)
        {
            node["properties"] = Json.FromObject(properties) ?? new JsonObject();
        }
    }
}
