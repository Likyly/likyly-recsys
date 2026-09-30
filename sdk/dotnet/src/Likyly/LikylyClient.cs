using Likyly.Internal;

namespace Likyly;

/// <summary>Client configuration. Only <see cref="ApiKey"/> is required.</summary>
public sealed class LikylyClientOptions
{
    /// <summary>
    /// Your API key. The <b>secret</b> key (backend only: items, users, everything) or the <b>public</b> key (recommendations
    /// and event tracking only). Never put the secret key in code that ships to a browser.
    /// </summary>
    public required string ApiKey { get; init; }

    /// <summary>Defaults to <c>https://api.likyly.com</c>.</summary>
    public string BaseUrl { get; init; } = LikylyClient.DefaultBaseUrl;

    /// <summary>Which of your catalogs to use, if your account has several (omit it if you have one).</summary>
    public string? Catalog { get; init; }

    /// <summary>Per-request timeout. Default 10 s.</summary>
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(10);

    /// <summary>Automatic retries on transient failures - 429, 502, 503, 504, dropped connections. Default 2; 0 disables.</summary>
    public int MaxRetries { get; init; } = 2;

    /// <summary>Appended to the SDK's User-Agent, e.g. <c>my-shop/1.4</c>.</summary>
    public string? UserAgent { get; init; }
}

/// <summary>
/// The LIKYLY client. Four things to know:
/// <code>
/// client.Items            your catalog
/// client.Users            your users (optional)
/// client.Events           what visitors do
/// client.Recommendations  what to show them
/// </code>
/// Thread-safe: create one and share it. With dependency injection, register it as a singleton and pass an
/// <c>HttpClient</c> from <c>IHttpClientFactory</c>: <c>new LikylyClient(options, httpClientFromFactory)</c>.
/// </summary>
public sealed class LikylyClient
{
    public const string Version = "1.0.0";
    public const string DefaultBaseUrl = "https://api.likyly.com";

    /// <summary>Uses the given API key and the defaults.</summary>
    public LikylyClient(string apiKey) : this(new LikylyClientOptions { ApiKey = apiKey })
    {
    }

    /// <param name="options">Configuration.</param>
    /// <param name="httpClient">Your own <c>HttpClient</c> (proxy, handler, IHttpClientFactory). Its <c>Timeout</c> is not used - the SDK applies <see cref="LikylyClientOptions.Timeout"/> per request.</param>
    public LikylyClient(LikylyClientOptions options, HttpClient? httpClient = null)
        : this(options, httpClient, null, null)
    {
    }

    internal LikylyClient(LikylyClientOptions options, HttpClient? httpClient, Func<TimeSpan, CancellationToken, Task>? delay, Func<double>? random)
    {
        if (string.IsNullOrWhiteSpace(options?.ApiKey))
        {
            throw new ValidationException("ApiKey is required (create one in your LIKYLY account)");
        }

        var http = new HttpCore(
            httpClient ?? new HttpClient { Timeout = System.Threading.Timeout.InfiniteTimeSpan },
            options.ApiKey,
            options.BaseUrl.TrimEnd('/'),
            options.Catalog,
            options.Timeout,
            options.MaxRetries,
            options.UserAgent is null ? $"likyly-dotnet/{Version}" : $"likyly-dotnet/{Version} {options.UserAgent}");
        if (delay is not null)
        {
            http.Delay = delay;
        }

        if (random is not null)
        {
            http.Random = random;
        }

        Items = new ItemsResource(http);
        Users = new UsersResource(http);
        Events = new EventsResource(http);
        Recommendations = new RecommendationsResource(http);
    }

    public ItemsResource Items { get; }

    public UsersResource Users { get; }

    public EventsResource Events { get; }

    public RecommendationsResource Recommendations { get; }
}
