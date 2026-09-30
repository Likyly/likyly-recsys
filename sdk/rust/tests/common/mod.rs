#![allow(dead_code)]

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use likyly::{HttpRequest, HttpResponse, Likyly, LikylyBuilder, Transport, TransportError, TransportFuture};
use serde_json::{json, Value};

type TestSleeper = Arc<dyn Fn(Duration) -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;

/// One scripted HTTP outcome. The last step repeats once the script is exhausted.
#[derive(Clone)]
pub struct Step {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Option<Value>,
    pub raw: Option<String>,
    pub fail: Option<TransportError>,
    pub hang: bool,
}

impl Step {
    pub fn ok(body: Value) -> Step {
        Step { status: 200, headers: vec![], body: Some(body), raw: None, fail: None, hang: false }
    }
    pub fn status(status: u16, body: Value) -> Step {
        Step { status, ..Step::ok(body) }
    }
    pub fn raw(status: u16, raw: &str) -> Step {
        Step { status, body: None, raw: Some(raw.to_string()), ..Step::ok(Value::Null) }
    }
    pub fn fail(error: TransportError) -> Step {
        Step { fail: Some(error), body: None, ..Step::ok(Value::Null) }
    }
    pub fn timeout() -> Step {
        Step::fail(TransportError::Timeout)
    }
    pub fn header(mut self, k: &str, v: &str) -> Step {
        self.headers.push((k.to_string(), v.to_string()));
        self
    }
    pub fn down() -> Step {
        Step::status(503, json!({ "detail": "down" }))
    }
}

pub struct FakeTransport {
    steps: Vec<Step>,
    calls: Mutex<Vec<HttpRequest>>,
}

impl FakeTransport {
    pub fn calls(&self) -> Vec<HttpRequest> {
        self.calls.lock().unwrap().clone()
    }
    pub fn count(&self) -> usize {
        self.calls.lock().unwrap().len()
    }
}

impl Transport for FakeTransport {
    fn send(&self, request: HttpRequest) -> TransportFuture<'_> {
        let step = {
            let mut calls = self.calls.lock().unwrap();
            calls.push(request);
            self.steps[(calls.len() - 1).min(self.steps.len() - 1)].clone()
        };
        Box::pin(async move {
            if step.hang {
                std::future::pending::<()>().await;
            }
            if let Some(e) = step.fail {
                return Err(e);
            }
            let body = match (&step.raw, &step.body) {
                (Some(raw), _) => raw.clone().into_bytes(),
                (None, Some(Value::Null)) | (None, None) => vec![],
                (None, Some(v)) => serde_json::to_vec(v).unwrap(),
            };
            let headers: HashMap<String, String> = step.headers.iter().map(|(k, v)| (k.to_ascii_lowercase(), v.clone())).collect();
            Ok(HttpResponse { status: step.status, headers, body })
        })
    }
}

pub struct Harness {
    pub client: Likyly,
    pub transport: Arc<FakeTransport>,
    pub sleeps: Arc<Mutex<Vec<Duration>>>,
}

impl Harness {
    pub fn sleeps(&self) -> Vec<Duration> {
        self.sleeps.lock().unwrap().clone()
    }
    pub fn call(&self, i: usize) -> HttpRequest {
        self.transport.calls()[i].clone()
    }
    pub fn body(&self, i: usize) -> Value {
        serde_json::from_slice(&self.call(i).body.expect("request has a body")).unwrap()
    }
}

pub const BASE_URL: &str = "https://api.example.test";
pub const API_KEY: &str = "sk_test_conformance";

/// A client over a scripted transport: no real waiting, jitter pinned to 1 (so a backoff is exactly base * 2^attempt).
pub fn harness_with(steps: Vec<Step>, configure: impl FnOnce(LikylyBuilder) -> LikylyBuilder) -> Harness {
    let transport = Arc::new(FakeTransport { steps, calls: Mutex::new(vec![]) });
    let sleeps = Arc::new(Mutex::new(vec![]));
    let recorded = sleeps.clone();
    let sleeper: TestSleeper = Arc::new(move |d| {
        recorded.lock().unwrap().push(d);
        Box::pin(async {})
    });
    let builder = Likyly::builder(API_KEY).base_url(BASE_URL).transport(transport.clone()).testing_hooks(sleeper, Arc::new(|| 1.0));
    let client = configure(builder).build().unwrap();
    Harness { client, transport, sleeps }
}

pub fn harness(steps: Vec<Step>) -> Harness {
    harness_with(steps, |b| b)
}

pub fn rec() -> Value {
    json!({ "recommendation_id": "rec_1", "strategy": "popular", "items": [] })
}
pub fn evt() -> Value {
    json!({ "message": "ok", "event_id": null, "duplicate": false })
}
pub fn item() -> Value {
    json!({ "item_id": "a", "title": "t" })
}

/// Splits a URL into (path as sent, decoded query pairs).
pub fn split_url(url: &str) -> (String, HashMap<String, String>) {
    let rest = url.split_once("://").map_or(url, |(_, r)| r);
    let path_and_query = rest.find('/').map_or("", |i| &rest[i..]);
    let (path, query) = path_and_query.split_once('?').unwrap_or((path_and_query, ""));
    let pairs = query
        .split('&')
        .filter(|p| !p.is_empty())
        .map(|p| {
            let (k, v) = p.split_once('=').unwrap_or((p, ""));
            (percent_decode(k), percent_decode(v))
        })
        .collect();
    (path.to_string(), pairs)
}

pub fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            out.push(u8::from_str_radix(&s[i + 1..i + 3], 16).unwrap());
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).unwrap()
}

pub fn header<'a>(request: &'a HttpRequest, name: &str) -> Option<&'a str> {
    request.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
}
