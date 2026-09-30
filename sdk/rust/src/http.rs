use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::encoding::encode;
use crate::error::{ApiError, Error, NetworkError, TimeoutError};

const RETRY_BASE: f64 = 0.5;
const RETRY_CAP: f64 = 8.0;
/// A `Retry-After` longer than this is not waited for: the `RateLimit` error is returned instead.
const MAX_RETRY_AFTER: Duration = Duration::from_secs(60);

/// One HTTP request, as handed to a [`Transport`].
#[derive(Debug, Clone)]
pub struct HttpRequest {
    pub method: String,
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Option<Vec<u8>>,
    pub timeout: Duration,
}

/// One HTTP response, as returned by a [`Transport`]. Header names must be lower-cased.
#[derive(Debug, Clone)]
pub struct HttpResponse {
    pub status: u16,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
}

/// Why a [`Transport`] could not produce a response.
#[derive(Debug, Clone)]
pub enum TransportError {
    /// The request's timeout elapsed.
    Timeout,
    /// DNS, connection, TLS, ... - no response.
    Network(String),
}

/// The boxed future a [`Transport`] returns.
pub type TransportFuture<'a> = Pin<Box<dyn Future<Output = Result<HttpResponse, TransportError>> + Send + 'a>>;

/// Sends one HTTP request. The default is [`ReqwestTransport`]; implement this to use another HTTP stack, add
/// tracing, or fake the API in your own tests. Retries, auth, and error mapping stay in the SDK.
pub trait Transport: Send + Sync {
    fn send(&self, request: HttpRequest) -> TransportFuture<'_>;
}

/// The default transport, built on `reqwest` with rustls.
#[derive(Debug, Clone, Default)]
pub struct ReqwestTransport {
    client: reqwest::Client,
}

impl ReqwestTransport {
    /// Uses your own `reqwest::Client` (proxy, custom root certificates, connection pool settings).
    pub fn with_client(client: reqwest::Client) -> Self {
        Self { client }
    }
}

impl Transport for ReqwestTransport {
    fn send(&self, request: HttpRequest) -> TransportFuture<'_> {
        Box::pin(async move {
            let method = reqwest::Method::from_bytes(request.method.as_bytes()).map_err(|e| TransportError::Network(e.to_string()))?;
            let mut builder = self.client.request(method, &request.url).timeout(request.timeout);
            for (k, v) in &request.headers {
                builder = builder.header(k, v);
            }
            if let Some(body) = request.body {
                builder = builder.body(body);
            }
            let classify =
                |e: reqwest::Error| if e.is_timeout() { TransportError::Timeout } else { TransportError::Network(error_chain(&e)) };
            let response = builder.send().await.map_err(classify)?;
            let status = response.status().as_u16();
            let headers = response
                .headers()
                .iter()
                .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str().to_ascii_lowercase(), v.to_string())))
                .collect();
            let body = response.bytes().await.map_err(classify)?.to_vec();
            Ok(HttpResponse { status, headers, body })
        })
    }
}

fn error_chain(e: &dyn std::error::Error) -> String {
    let mut message = e.to_string();
    let mut source = e.source();
    while let Some(s) = source {
        message.push_str(": ");
        message.push_str(&s.to_string());
        source = s.source();
    }
    message
}

pub(crate) type Sleeper = Arc<dyn Fn(Duration) -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;
pub(crate) type Random = Arc<dyn Fn() -> f64 + Send + Sync>;

pub(crate) fn default_sleeper() -> Sleeper {
    Arc::new(|d| Box::pin(tokio::time::sleep(d)))
}

/// Uniform in [0, 1) without a `rand` dependency: std's per-instance random hasher seed.
pub(crate) fn default_random() -> Random {
    Arc::new(|| {
        use std::hash::{BuildHasher, Hasher};
        let bits = std::collections::hash_map::RandomState::new().build_hasher().finish();
        (bits >> 11) as f64 / (1u64 << 53) as f64
    })
}

pub(crate) struct Call {
    pub method: &'static str,
    pub path: String,
    pub query: Vec<(String, String)>,
    pub body: Option<Value>,
    /// Whether the call is safe to send again if its outcome is unknown (a timeout, a 5xx, a dropped connection):
    /// false for events without an `event_id`, where a blind retry could record the event twice.
    pub idempotent: bool,
}

pub(crate) struct Response {
    pub status: u16,
    pub headers: HashMap<String, String>,
    body: Vec<u8>,
}

impl Response {
    pub fn json<T: DeserializeOwned>(&self) -> Result<T, Error> {
        serde_json::from_slice(&self.body).map_err(|e| {
            Error::Api(Box::new(ApiError {
                status_code: self.status,
                message: format!("the API answered with a body that is not the expected JSON: {e}"),
                request_id: None,
                retry_after: None,
                body: None,
            }))
        })
    }
}

/// The only place that talks HTTP. Every resource goes through `request`: auth header, catalog parameter,
/// timeout, retries with exponential backoff + jitter, and the error mapping.
#[derive(Clone)]
pub(crate) struct Core {
    pub transport: Arc<dyn Transport>,
    pub api_key: Arc<str>,
    pub base_url: Arc<str>,
    pub catalog: Option<Arc<str>>,
    pub timeout: Duration,
    pub max_retries: u32,
    pub user_agent: Arc<str>,
    pub sleeper: Sleeper,
    pub random: Random,
}

impl Core {
    pub async fn request(&self, call: Call) -> Result<Response, Error> {
        let url = self.build_url(&call.path, &call.query);
        let payload = call
            .body
            .as_ref()
            .map(serde_json::to_vec)
            .transpose()
            .map_err(|e| Error::validation(format!("request body is not valid JSON: {e}")))?;
        let mut attempt = 0u32;
        loop {
            match self.once(call.method, &url, payload.clone()).await {
                Ok(response) => return Ok(response),
                Err(error) => {
                    let Some(delay) = self.retry_delay(&error, attempt, call.idempotent) else { return Err(error) };
                    if attempt >= self.max_retries {
                        return Err(error);
                    }
                    (self.sleeper)(delay).await;
                    attempt += 1;
                }
            }
        }
    }

    fn retry_delay(&self, error: &Error, attempt: u32, idempotent: bool) -> Option<Duration> {
        let backoff = Duration::from_secs_f64((self.random)() * RETRY_CAP.min(RETRY_BASE * 2f64.powi(attempt as i32))); // full jitter
        match error {
            Error::Network(_) | Error::Timeout(_) => idempotent.then_some(backoff),
            other => {
                let api = other.api()?;
                let after_header = || match api.retry_after {
                    None => Some(backoff),
                    Some(d) if d <= MAX_RETRY_AFTER => Some(d),
                    Some(_) => None,
                };
                if api.status_code == 429 {
                    // Rejected by the rate limiter before reaching the application: nothing was processed, so retrying is safe for every request.
                    return after_header();
                }
                if !idempotent {
                    return None; // the outcome is unknown - never risk a duplicate
                }
                matches!(api.status_code, 502..=504).then(after_header).flatten()
            }
        }
    }

    fn build_url(&self, path: &str, query: &[(String, String)]) -> String {
        let mut url = format!("{}{}", self.base_url, path);
        let catalog = self.catalog.as_ref().map(|c| ("data_product_type".to_string(), c.to_string()));
        for (i, (k, v)) in query.iter().chain(catalog.iter()).enumerate() {
            url.push(if i == 0 { '?' } else { '&' });
            url.push_str(&encode(k));
            url.push('=');
            url.push_str(&encode(v));
        }
        url
    }

    async fn once(&self, method: &str, url: &str, payload: Option<Vec<u8>>) -> Result<Response, Error> {
        let mut headers = vec![
            ("X-API-Key".to_string(), self.api_key.to_string()),
            ("Accept".to_string(), "application/json".to_string()),
            ("User-Agent".to_string(), self.user_agent.to_string()),
        ];
        if payload.is_some() {
            headers.push(("Content-Type".to_string(), "application/json".to_string()));
        }
        let request = HttpRequest { method: method.to_string(), url: url.to_string(), headers, body: payload, timeout: self.timeout };
        let response = self.transport.send(request).await.map_err(|e| match e {
            TransportError::Timeout => Error::Timeout(TimeoutError { timeout: self.timeout }),
            TransportError::Network(message) => Error::Network(NetworkError { message }),
        })?;

        if response.status < 400 {
            return Ok(Response { status: response.status, headers: response.headers, body: response.body });
        }
        let body = if response.body.is_empty() {
            None
        } else {
            Some(
                serde_json::from_slice(&response.body)
                    .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&response.body).into_owned())),
            )
        };
        let request_id =
            response.headers.get("x-request-id").cloned().or_else(|| {
                body.as_ref().and_then(|b| b.get("request_id")).map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_string))
            });
        let retry_after = response.headers.get("retry-after").and_then(|v| parse_retry_after(v));
        Err(Error::from_status(response.status, body, request_id, retry_after))
    }
}

/// Delta-seconds or an HTTP date.
fn parse_retry_after(value: &str) -> Option<Duration> {
    let value = value.trim();
    if let Ok(seconds) = value.parse::<f64>() {
        return (seconds >= 0.0 && seconds.is_finite()).then(|| Duration::from_secs_f64(seconds));
    }
    let when = httpdate::parse_http_date(value).ok()?;
    Some(when.duration_since(std::time::SystemTime::now()).unwrap_or(Duration::ZERO))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_random_is_uniform_in_the_unit_interval() {
        let random = default_random();
        let samples: Vec<f64> = (0..200).map(|_| random()).collect();
        assert!(samples.iter().all(|v| (0.0..1.0).contains(v)));
        assert!(samples.windows(2).any(|w| w[0] != w[1]), "not constant");
        assert!(samples.iter().any(|v| *v < 0.25) && samples.iter().any(|v| *v > 0.75), "spread over the interval");
    }

    #[test]
    fn retry_after_parses_seconds_dates_and_rejects_garbage() {
        assert_eq!(parse_retry_after("7"), Some(Duration::from_secs(7)));
        assert_eq!(parse_retry_after(" 0 "), Some(Duration::ZERO));
        assert_eq!(parse_retry_after("1.5"), Some(Duration::from_millis(1500)));
        assert_eq!(parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), Some(Duration::ZERO)); // in the past
        assert_eq!(parse_retry_after("soon"), None);
        assert_eq!(parse_retry_after("-3"), None);
    }
}
