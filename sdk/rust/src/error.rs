use std::error::Error as StdError;
use std::fmt;
use std::time::Duration;

use serde_json::Value;

/// What the API answered to a failed call.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    /// The HTTP status code.
    pub status_code: u16,
    /// The API's explanation.
    pub message: String,
    /// The `request_id` of the failed call (also the `X-Request-ID` header) - quote it when reporting a problem.
    pub request_id: Option<String>,
    /// The `Retry-After` header (429/503), when present.
    pub retry_after: Option<Duration>,
    /// The parsed response body, when there was one (a JSON value, or a string for a non-JSON body).
    pub body: Option<Value>,
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} (HTTP {}", self.message, self.status_code)?;
        if let Some(id) = &self.request_id {
            write!(f, ", request_id={id}")?;
        }
        write!(f, ")")
    }
}

impl StdError for ApiError {}

/// An invalid request: caught by the SDK before anything is sent (`api` is `None`), or a 422 from the API.
#[derive(Debug, Clone, PartialEq)]
pub struct ValidationError {
    pub message: String,
    /// Set when the error came from a 422 response.
    pub api: Option<Box<ApiError>>,
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.api {
            Some(api) => api.fmt(f),
            None => write!(f, "{}", self.message),
        }
    }
}

impl StdError for ValidationError {}

/// No HTTP response: DNS failure, connection refused or reset, TLS error, ...
#[derive(Debug, Clone, PartialEq)]
pub struct NetworkError {
    pub message: String,
}

impl fmt::Display for NetworkError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "could not reach the API: {}", self.message)
    }
}

impl StdError for NetworkError {}

/// The request timed out (the client's `timeout`).
#[derive(Debug, Clone, PartialEq)]
pub struct TimeoutError {
    pub timeout: Duration,
}

impl fmt::Display for TimeoutError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "request timed out after {:?}", self.timeout)
    }
}

impl StdError for TimeoutError {}

/// Every error the SDK returns.
///
/// ```text
/// Error
/// ├── Validation        invalid request - caught by the SDK before sending, or a 422 from the API
/// ├── Network           no HTTP response
/// ├── Timeout           the request timed out
/// └── Api-derived       the API answered with an error status:
///     ├── Authentication    401 - missing / invalid / revoked API key
///     ├── PermissionDenied  403 - e.g. a public key used for a secret-key operation, or a plan limit
///     ├── NotFound          404
///     ├── RateLimit         429 - see `retry_after`
///     └── Api               any other status
/// ```
///
/// The API-derived variants box their [`ApiError`] (it derefs transparently: `e.retry_after`, `e.message`) to keep
/// `Result<_, Error>` small. The enum is `#[non_exhaustive]`: keep a wildcard arm. [`Error::status_code`], [`Error::request_id`],
/// [`Error::retry_after`] and [`Error::body`] work on every variant that carries them.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum Error {
    Validation(ValidationError),
    Network(NetworkError),
    Timeout(TimeoutError),
    Authentication(Box<ApiError>),
    PermissionDenied(Box<ApiError>),
    NotFound(Box<ApiError>),
    RateLimit(Box<ApiError>),
    Api(Box<ApiError>),
}

impl Error {
    pub(crate) fn validation(message: impl Into<String>) -> Self {
        Error::Validation(ValidationError { message: message.into(), api: None })
    }

    pub(crate) fn from_status(status: u16, body: Option<Value>, request_id: Option<String>, retry_after: Option<Duration>) -> Self {
        let message = body.as_ref().and_then(message_of).unwrap_or_else(|| format!("API error (HTTP {status})"));
        let api = Box::new(ApiError { status_code: status, message, request_id, retry_after, body });
        match status {
            401 => Error::Authentication(api),
            403 => Error::PermissionDenied(api),
            404 => Error::NotFound(api),
            422 => Error::Validation(ValidationError { message: api.message.clone(), api: Some(api) }),
            429 => Error::RateLimit(api),
            _ => Error::Api(api),
        }
    }

    /// The API's answer, for every variant that came from an HTTP response.
    pub fn api(&self) -> Option<&ApiError> {
        match self {
            Error::Authentication(a) | Error::PermissionDenied(a) | Error::NotFound(a) | Error::RateLimit(a) | Error::Api(a) => Some(a),
            Error::Validation(v) => v.api.as_deref(),
            Error::Network(_) | Error::Timeout(_) => None,
        }
    }

    /// The HTTP status code, when the API answered.
    pub fn status_code(&self) -> Option<u16> {
        self.api().map(|a| a.status_code)
    }

    /// The `request_id` of the failed call, when the API answered.
    pub fn request_id(&self) -> Option<&str> {
        self.api().and_then(|a| a.request_id.as_deref())
    }

    /// The `Retry-After` header, when present.
    pub fn retry_after(&self) -> Option<Duration> {
        self.api().and_then(|a| a.retry_after)
    }

    /// The parsed response body, when there was one.
    pub fn body(&self) -> Option<&Value> {
        self.api().and_then(|a| a.body.as_ref())
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Validation(e) => e.fmt(f),
            Error::Network(e) => e.fmt(f),
            Error::Timeout(e) => e.fmt(f),
            Error::Authentication(e) | Error::PermissionDenied(e) | Error::NotFound(e) | Error::RateLimit(e) | Error::Api(e) => e.fmt(f),
        }
    }
}

impl StdError for Error {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Error::Validation(e) => Some(e),
            Error::Network(e) => Some(e),
            Error::Timeout(e) => Some(e),
            Error::Authentication(e) | Error::PermissionDenied(e) | Error::NotFound(e) | Error::RateLimit(e) | Error::Api(e) => Some(e),
        }
    }
}

fn message_of(body: &Value) -> Option<String> {
    match body.get("detail")? {
        Value::String(s) => Some(s.clone()),
        Value::Array(items) => Some(
            items
                .iter()
                .map(|d| match d.get("msg") {
                    Some(Value::String(m)) => m.clone(),
                    _ => d.to_string(),
                })
                .collect::<Vec<_>>()
                .join("; "),
        ),
        Value::Null => None,
        other => Some(other.to_string()),
    }
}
