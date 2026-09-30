namespace Likyly;

/// <summary>
/// Every exception the SDK throws extends <see cref="LikylyException"/>:
/// <code>
/// LikylyException
/// ├─ NetworkException              no HTTP response (DNS, connection reset, ...)
/// ├─ RequestTimeoutException       the request timed out
/// ├─ ValidationException           invalid request - caught by the SDK before sending, or 422 from the API
/// └─ ApiException                  the API answered with an error status
///    ├─ AuthenticationException    401 - missing / invalid / revoked API key
///    ├─ PermissionDeniedException  403 - e.g. a public key used for a secret-key operation, or a plan limit
///    ├─ NotFoundException          404
///    └─ RateLimitException         429 - see <see cref="LikylyException.RetryAfter"/>
/// </code>
/// </summary>
public class LikylyException : Exception
{
    public LikylyException(string message, int? statusCode = null, string? requestId = null, double? retryAfter = null, object? body = null, Exception? inner = null)
        : base(message, inner)
    {
        StatusCode = statusCode;
        RequestId = requestId;
        RetryAfter = retryAfter;
        Body = body;
    }

    /// <summary>HTTP status code, when there was an HTTP response.</summary>
    public int? StatusCode { get; }

    /// <summary>The <c>request_id</c> of the failed call (also the <c>X-Request-ID</c> header) - quote it when reporting a problem.</summary>
    public string? RequestId { get; }

    /// <summary>Seconds to wait before retrying, from the <c>Retry-After</c> header (429/503).</summary>
    public double? RetryAfter { get; }

    /// <summary>The parsed response body (a Dictionary, List or string), when there was one.</summary>
    public object? Body { get; }

    internal static LikylyException FromResponse(int status, object? body, string? requestId, double? retryAfter)
    {
        var message = MessageOf(body) ?? $"LIKYLY API error (HTTP {status})";
        return status switch
        {
            401 => new AuthenticationException(message, status, requestId, retryAfter, body),
            403 => new PermissionDeniedException(message, status, requestId, retryAfter, body),
            404 => new NotFoundException(message, status, requestId, retryAfter, body),
            422 => new ValidationException(message, status, requestId, retryAfter, body),
            429 => new RateLimitException(message, status, requestId, retryAfter, body),
            _ => new ApiException(message, status, requestId, retryAfter, body),
        };
    }

    private static string? MessageOf(object? body)
    {
        if (body is Dictionary<string, object?> map && map.TryGetValue("detail", out var detail))
        {
            if (detail is string s)
            {
                return s;
            }

            if (detail is List<object?> list)
            {
                return string.Join("; ", list.Select(d => d is Dictionary<string, object?> m && m.TryGetValue("msg", out var msg) ? msg?.ToString() : d?.ToString()));
            }

            return detail?.ToString();
        }

        return null;
    }
}

/// <summary>No HTTP response: DNS, connection reset, ...</summary>
public class NetworkException : LikylyException
{
    public NetworkException(string message, Exception? inner = null) : base(message, inner: inner) { }
}

/// <summary>The request timed out.</summary>
public class RequestTimeoutException : LikylyException
{
    public RequestTimeoutException(string message, Exception? inner = null) : base(message, inner: inner) { }
}

/// <summary>Invalid request: caught by the SDK before sending, or a 422 from the API.</summary>
public class ValidationException : LikylyException
{
    public ValidationException(string message, int? statusCode = null, string? requestId = null, double? retryAfter = null, object? body = null)
        : base(message, statusCode, requestId, retryAfter, body) { }
}

/// <summary>The API answered with an error status.</summary>
public class ApiException : LikylyException
{
    public ApiException(string message, int? statusCode, string? requestId, double? retryAfter, object? body)
        : base(message, statusCode, requestId, retryAfter, body) { }
}

/// <summary>401 - missing, invalid or revoked API key.</summary>
public class AuthenticationException : ApiException
{
    public AuthenticationException(string message, int? statusCode, string? requestId, double? retryAfter, object? body)
        : base(message, statusCode, requestId, retryAfter, body) { }
}

/// <summary>403 - e.g. a public key used for a secret-key operation, or a plan limit.</summary>
public class PermissionDeniedException : ApiException
{
    public PermissionDeniedException(string message, int? statusCode, string? requestId, double? retryAfter, object? body)
        : base(message, statusCode, requestId, retryAfter, body) { }
}

/// <summary>404.</summary>
public class NotFoundException : ApiException
{
    public NotFoundException(string message, int? statusCode, string? requestId, double? retryAfter, object? body)
        : base(message, statusCode, requestId, retryAfter, body) { }
}

/// <summary>429 - see <see cref="LikylyException.RetryAfter"/>.</summary>
public class RateLimitException : ApiException
{
    public RateLimitException(string message, int? statusCode, string? requestId, double? retryAfter, object? body)
        : base(message, statusCode, requestId, retryAfter, body) { }
}
