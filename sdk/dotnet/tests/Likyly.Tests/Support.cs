using System.Net;
using System.Text;
using Likyly;

namespace Likyly.Tests;

/// <summary>One scripted step: a response, or an exception to throw.</summary>
internal sealed record Step(int Status = 200, string? Body = null, Dictionary<string, string>? Headers = null, Exception? Failure = null);

internal sealed record Recorded(HttpMethod Method, string RawPathAndQuery, Uri Uri, Dictionary<string, string> Headers, string? ContentType, string? Body);

/// <summary>Replays scripted responses in order (the last one repeats) and records every request.</summary>
internal sealed class FakeHandler : HttpMessageHandler
{
    private readonly List<Step> _script;
    private int _index;

    public FakeHandler(params Step[] script) => _script = script.ToList();

    public List<Recorded> Calls { get; } = [];

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        var headers = request.Headers.ToDictionary(h => h.Key, h => string.Join(", ", h.Value), StringComparer.OrdinalIgnoreCase);
        headers["User-Agent"] = request.Headers.UserAgent.ToString(); // as sent on the wire (product tokens joined by a space)
        var body = request.Content is null ? null : await request.Content.ReadAsStringAsync(ct);
        var raw = request.RequestUri!.OriginalString;
        Calls.Add(new Recorded(request.Method, raw[raw.IndexOf('/', raw.IndexOf("//", StringComparison.Ordinal) + 2)..], request.RequestUri, headers, request.Content?.Headers.ContentType?.MediaType, body));

        var step = _script[Math.Min(_index++, _script.Count - 1)];
        if (step.Failure is not null)
        {
            throw step.Failure;
        }

        var response = new HttpResponseMessage((HttpStatusCode)step.Status);
        if (step.Body is not null)
        {
            response.Content = new StringContent(step.Body, Encoding.UTF8, "application/json");
        }

        foreach (var (key, value) in step.Headers ?? [])
        {
            response.Headers.TryAddWithoutValidation(key, value);
        }

        return response;
    }
}

internal static class Harness
{
    public static (LikylyClient Client, FakeHandler Handler, List<TimeSpan> Sleeps) Make(int maxRetries, params Step[] script) =>
        Make(new LikylyClientOptions { ApiKey = "sk_test", BaseUrl = "https://api.example.test", MaxRetries = maxRetries, Timeout = TimeSpan.FromSeconds(5) }, script);

    public static (LikylyClient Client, FakeHandler Handler, List<TimeSpan> Sleeps) Make(LikylyClientOptions options, params Step[] script)
    {
        var handler = new FakeHandler(script);
        var sleeps = new List<TimeSpan>();
        var client = new LikylyClient(options, new HttpClient(handler), (d, _) => { sleeps.Add(d); return Task.CompletedTask; }, () => 1.0);
        return (client, handler, sleeps);
    }
}
