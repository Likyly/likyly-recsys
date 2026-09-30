using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Xunit;

namespace Likyly.Tests;

/// <summary>
/// Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK runs, and that
/// validate_against_openapi.py checks against the OpenAPI document. Generic: it calls the SDK by reflection (resource,
/// method name, JSON arguments converted to the parameter types), so a new scenario needs no C# code.
/// </summary>
public class ConformanceTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, PropertyNameCaseInsensitive = true };

    private static JsonNode File()
    {
        var dir = AppContext.BaseDirectory;
        while (!System.IO.File.Exists(Path.Combine(dir, "conformance", "scenarios.json")))
        {
            dir = Directory.GetParent(dir)?.FullName ?? throw new FileNotFoundException("conformance/scenarios.json");
        }

        return JsonNode.Parse(System.IO.File.ReadAllText(Path.Combine(dir, "conformance", "scenarios.json")))!;
    }

    public static IEnumerable<object[]> Scenarios() => File()["scenarios"]!.AsArray().Select(s => new object[] { s!["id"]!.GetValue<string>() });

    [Theory]
    [MemberData(nameof(Scenarios))]
    public async Task Scenario(string id)
    {
        var file = File();
        var defaults = file["defaults"]!;
        var scenario = file["scenarios"]!.AsArray().First(s => s!["id"]!.GetValue<string>() == id)!;
        var response = scenario["response"]!;

        var headers = response["headers"]!.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.GetValue<string>());
        var step = new Step(response["status"]!.GetValue<int>(), response["body"]?.ToJsonString(), headers);
        var options = new LikylyClientOptions
        {
            ApiKey = defaults["apiKey"]!.GetValue<string>(),
            BaseUrl = defaults["baseUrl"]!.GetValue<string>(),
            MaxRetries = 0,
            Catalog = scenario["config"]?["catalog"]?.GetValue<string>(),
        };
        var (client, handler, _) = Harness.Make(options, step);

        var call = scenario["call"]!;
        var resourceName = Pascal(call["resource"]!.GetValue<string>());
        var resource = typeof(LikylyClient).GetProperty(resourceName)!.GetValue(client)!;
        var method = resource.GetType().GetMethod(Pascal(call["method"]!.GetValue<string>()) + "Async")
                     ?? throw new Xunit.Sdk.XunitException($"{resourceName}.{call["method"]}Async does not exist");
        var args = BuildArgs(method, call["args"]!.AsArray());

        if (scenario["error"] is JsonNode want)
        {
            var e = await Assert.ThrowsAnyAsync<LikylyException>(() => Invoke(method, resource, args));
            Assert.Equal(want["class"]!.GetValue<string>().Replace("Error", "Exception"), e.GetType().Name);
            Assert.Equal(want["statusCode"]!.GetValue<int>(), e.StatusCode);
            if (want["requestId"] is JsonNode rid)
            {
                Assert.Equal(rid.GetValue<string>(), e.RequestId);
            }

            if (want["retryAfter"] is JsonNode ra)
            {
                Assert.Equal(ra.GetValue<double>(), e.RetryAfter);
            }

            if (want["message"] is JsonNode msg)
            {
                Assert.Equal(msg.GetValue<string>(), e.Message);
            }
        }
        else
        {
            var result = JsonSerializer.SerializeToNode(await Invoke(method, resource, args), Json);
            foreach (var (path, expected) in scenario["expect"]?.AsObject() ?? [])
            {
                AssertJson(expected, Pick(result, path), $"result.{path}");
            }
        }

        AssertRequest(scenario, handler, defaults);
    }

    private static string Pascal(string name) => char.ToUpperInvariant(name[0]) + name[1..];

    private static object?[] BuildArgs(MethodInfo method, JsonArray jsonArgs)
    {
        var parameters = method.GetParameters();
        var args = new object?[parameters.Length];
        for (var i = 0; i < parameters.Length; i++)
        {
            var p = parameters[i];
            if (i < jsonArgs.Count)
            {
                args[i] = JsonSerializer.Deserialize(jsonArgs[i]!.ToJsonString(), p.ParameterType, Json);
            }
            else
            {
                args[i] = p.ParameterType == typeof(CancellationToken) ? CancellationToken.None : null;
            }
        }

        return args;
    }

    private static async Task<object?> Invoke(MethodInfo method, object target, object?[] args)
    {
        var task = (Task)method.Invoke(target, args)!;
        await task;
        return task.GetType().IsGenericType ? task.GetType().GetProperty("Result")!.GetValue(task) : null;
    }

    private static JsonNode? Pick(JsonNode? node, string path)
    {
        foreach (var key in path.Split('.'))
        {
            if (node is null)
            {
                return null;
            }

            node = key == "length" ? JsonValue.Create(node.AsArray().Count) : int.TryParse(key, out var i) ? node.AsArray()[i] : node[key];
        }

        return node;
    }

    private static void AssertJson(JsonNode? want, JsonNode? got, string where)
    {
        Assert.True(got is not null || want is null, $"{where} is missing");
        if (want is JsonValue w && got is JsonValue g && w.TryGetValue<double>(out var wd) && g.TryGetValue<double>(out var gd))
        {
            Assert.Equal(wd, gd, 9);
        }
        else
        {
            Assert.Equal(want?.ToJsonString(), got?.ToJsonString());
        }
    }

    private static void AssertRequest(JsonNode scenario, FakeHandler handler, JsonNode defaults)
    {
        var call = Assert.Single(handler.Calls);
        var want = scenario["request"]!;
        Assert.Equal(want["method"]!.GetValue<string>(), call.Method.Method);
        Assert.Equal(defaults["baseUrl"]!.GetValue<string>(), $"{call.Uri.Scheme}://{call.Uri.Authority}");

        var pathAndQuery = call.RawPathAndQuery.Split('?', 2);
        Assert.Equal(want["path"]!.GetValue<string>(), pathAndQuery[0]); // raw request path

        var query = pathAndQuery.Length > 1
            ? pathAndQuery[1].Split('&').Select(p => p.Split('=', 2)).ToDictionary(kv => Uri.UnescapeDataString(kv[0]), kv => Uri.UnescapeDataString(kv[1]))
            : [];
        var wantQuery = want["query"]!.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.GetValue<string>());
        Assert.Equal(wantQuery.OrderBy(k => k.Key), query.OrderBy(k => k.Key));

        Assert.Equal(defaults["apiKey"]!.GetValue<string>(), call.Headers["X-API-Key"]);
        Assert.StartsWith(defaults["userAgentPrefix"]!.GetValue<string>(), call.Headers["User-Agent"]);
        Assert.Equal(want["body"] is not null ? "application/json" : null, call.ContentType);
        if (want["body"] is JsonNode body)
        {
            Assert.True(JsonNode.DeepEquals(body, JsonNode.Parse(call.Body!)), $"request body: {call.Body}");
        }
        else
        {
            Assert.Null(call.Body);
        }
    }
}
