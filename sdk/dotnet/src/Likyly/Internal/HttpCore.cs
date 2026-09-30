using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Likyly.Internal;

/// <summary>A parsed successful response.</summary>
internal sealed record Result(int Status, IReadOnlyDictionary<string, string> Headers, JsonNode? Data);

/// <summary>
/// The only place that talks HTTP. Every resource goes through <see cref="RequestAsync"/>: auth header, catalog
/// parameter, timeout, retries with exponential backoff + jitter, and the error mapping.
/// </summary>
internal sealed class HttpCore
{
    private const double RetryBaseMs = 500;
    private const double RetryCapMs = 8_000;

    /// <summary>A Retry-After longer than this is not waited for: the RateLimitException is thrown instead.</summary>
    private const double MaxRetryAfterSeconds = 60;

    private readonly HttpClient _http;
    private readonly string _apiKey;
    private readonly string _baseUrl;
    private readonly string? _catalog;
    private readonly TimeSpan _timeout;
    private readonly int _maxRetries;
    private readonly string _userAgent;

    /// <summary>Test hooks.</summary>
    internal Func<TimeSpan, CancellationToken, Task> Delay { get; set; } = Task.Delay;

    internal Func<double> Random { get; set; } = System.Random.Shared.NextDouble;

    public HttpCore(HttpClient http, string apiKey, string baseUrl, string? catalog, TimeSpan timeout, int maxRetries, string userAgent)
    {
        _http = http;
        _apiKey = apiKey;
        _baseUrl = baseUrl;
        _catalog = catalog;
        _timeout = timeout;
        _maxRetries = maxRetries;
        _userAgent = userAgent;
    }

    // idempotent: safe to send again if the outcome is unknown (a timeout, a 5xx, a dropped connection)? False for
    // events without an EventId, where a blind retry could record the event twice.
    public async Task<Result> RequestAsync(HttpMethod method, string path, IEnumerable<KeyValuePair<string, string>>? query, JsonObject? body, bool idempotent, CancellationToken ct)
    {
        var uri = BuildUri(path, query);
        var attempt = 0;
        while (true)
        {
            try
            {
                return await OnceAsync(method, uri, body, ct).ConfigureAwait(false);
            }
            catch (LikylyException error)
            {
                var delay = RetryDelay(error, attempt, idempotent);
                if (delay is null || attempt >= _maxRetries)
                {
                    throw;
                }

                attempt++;
                await Delay(delay.Value, ct).ConfigureAwait(false);
            }
        }
    }

    private TimeSpan? RetryDelay(LikylyException error, int attempt, bool idempotent)
    {
        var backoff = TimeSpan.FromMilliseconds(Random() * Math.Min(RetryCapMs, RetryBaseMs * Math.Pow(2, attempt))); // full jitter
        if (error.StatusCode == 429)
        {
            // Rejected by the rate limiter before reaching the application: nothing was processed, so retrying is safe for every request.
            return error.RetryAfter is { } ra ? AfterHeader(ra) : backoff;
        }

        if (!idempotent)
        {
            return null; // the outcome is unknown - never risk a duplicate
        }

        if (error.StatusCode is 502 or 503 or 504)
        {
            return error.RetryAfter is { } ra ? AfterHeader(ra) : backoff;
        }

        return error is NetworkException or RequestTimeoutException ? backoff : null;
    }

    private static TimeSpan? AfterHeader(double seconds) => seconds <= MaxRetryAfterSeconds ? TimeSpan.FromSeconds(seconds) : null;

    private Uri BuildUri(string path, IEnumerable<KeyValuePair<string, string>>? query)
    {
        var sb = new StringBuilder(_baseUrl).Append(path);
        var parameters = new List<KeyValuePair<string, string>>(query ?? []);
        if (!string.IsNullOrEmpty(_catalog))
        {
            parameters.Add(new("data_product_type", _catalog));
        }

        var separator = '?';
        foreach (var (key, value) in parameters)
        {
            sb.Append(separator).Append(Encode(key)).Append('=').Append(Encode(value));
            separator = '&';
        }

        return new Uri(sb.ToString(), UriKind.Absolute);
    }

    private async Task<Result> OnceAsync(HttpMethod method, Uri uri, JsonObject? body, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(method, uri);
        request.Headers.TryAddWithoutValidation("X-API-Key", _apiKey);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.TryAddWithoutValidation("User-Agent", _userAgent);
        if (body is not null)
        {
            request.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(_timeout);

        HttpResponseMessage response;
        string text;
        try
        {
            response = await _http.SendAsync(request, HttpCompletionOption.ResponseContentRead, timeout.Token).ConfigureAwait(false);
            text = await response.Content.ReadAsStringAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new RequestTimeoutException($"LIKYLY request timed out after {(int)_timeout.TotalMilliseconds} ms");
        }
        catch (HttpRequestException e)
        {
            throw new NetworkException($"Could not reach the LIKYLY API: {e.Message}", e);
        }

        using (response)
        {
            JsonNode? node = null;
            object? parsed = null;
            if (text.Length > 0)
            {
                try
                {
                    node = JsonNode.Parse(text);
                    parsed = Json.ToObject(node);
                }
                catch (JsonException)
                {
                    parsed = text; // e.g. an HTML 502 page from a proxy
                }
            }

            var headers = new Dictionary<string, string>();
            foreach (var h in response.Headers.Concat(response.Content.Headers))
            {
                headers[h.Key.ToLowerInvariant()] = string.Join(", ", h.Value);
            }

            var status = (int)response.StatusCode;
            if (status >= 400)
            {
                headers.TryGetValue("x-request-id", out var requestId);
                if (requestId is null && parsed is Dictionary<string, object?> m && m.TryGetValue("request_id", out var rid))
                {
                    requestId = rid?.ToString();
                }

                headers.TryGetValue("retry-after", out var retryAfter);
                throw LikylyException.FromResponse(status, parsed, requestId, ParseRetryAfter(retryAfter));
            }

            return new Result(status, headers, node);
        }
    }

    private static double? ParseRetryAfter(string? value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return null;
        }

        if (double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var seconds))
        {
            return seconds >= 0 ? seconds : null;
        }

        return DateTimeOffset.TryParse(value, out var date) ? Math.Max(0, (date - DateTimeOffset.UtcNow).TotalSeconds) : null;
    }

    /// <summary>Percent-encodes everything outside RFC 3986 unreserved (A-Z a-z 0-9 - . _ ~), '/' included.</summary>
    internal static string Encode(string value)
    {
        var sb = new StringBuilder();
        foreach (var b in Encoding.UTF8.GetBytes(value))
        {
            if (b is (>= (byte)'A' and <= (byte)'Z') or (>= (byte)'a' and <= (byte)'z') or (>= (byte)'0' and <= (byte)'9') or (byte)'-' or (byte)'.' or (byte)'_' or (byte)'~')
            {
                sb.Append((char)b);
            }
            else
            {
                sb.Append('%').Append(b.ToString("X2"));
            }
        }

        return sb.ToString();
    }
}
