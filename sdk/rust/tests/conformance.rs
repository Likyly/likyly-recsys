//! Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK runs, and that
//! validate_against_openapi.py checks against the OpenAPI document.

mod common;

use common::*;
use likyly::{Error, Likyly};
use serde::de::DeserializeOwned;
use serde_json::{json, Map, Value};

fn snake(name: &str) -> String {
    let mut out = String::new();
    for c in name.chars() {
        if c.is_ascii_uppercase() {
            out.push('_');
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

/// Scenario args are camelCase JSON. Only the *top-level* keys of option objects (and of list entries) become
/// snake_case - never anything inside `properties`, whose keys belong to the developer.
fn snake_keys(v: &Value) -> Value {
    match v {
        Value::Object(m) => Value::Object(m.iter().map(|(k, v)| (snake(k), v.clone())).collect::<Map<_, _>>()),
        Value::Array(a) => Value::Array(a.iter().map(snake_keys).collect()),
        other => other.clone(),
    }
}

fn arg<T: DeserializeOwned + Default>(args: &[Value], i: usize) -> T {
    args.get(i).map_or_else(T::default, |v| serde_json::from_value(v.clone()).unwrap_or_else(|e| panic!("arg {i}: {e}")))
}

fn text(args: &[Value], i: usize) -> String {
    args[i].as_str().unwrap().to_string()
}

fn ser<T: serde::Serialize>(r: Result<T, Error>) -> Result<Value, Error> {
    r.map(|v| serde_json::to_value(v).unwrap())
}

async fn invoke(c: &Likyly, resource: &str, method: &str, raw_args: &[Value]) -> Result<Value, Error> {
    let args: Vec<Value> = raw_args.iter().map(snake_keys).collect();
    let a = &args[..];
    match (resource, method) {
        ("items", "get") => ser(c.items().get(&text(a, 0)).await),
        ("items", "list") => ser(c.items().list(arg(a, 0)).await),
        ("items", "upsert") => ser(c.items().upsert(&text(a, 0), arg(a, 1)).await),
        ("items", "delete") => c.items().delete(&text(a, 0)).await.map(|_| Value::Null),
        ("items", "upsertMany") => ser(c.items().upsert_many(&arg::<Vec<likyly::ItemImport>>(a, 0)).await),
        ("items", "deleteMany") => ser(c.items().delete_many(&arg::<Vec<String>>(a, 0)).await),
        ("users", "get") => ser(c.users().get(&text(a, 0)).await),
        ("users", "list") => ser(c.users().list(arg(a, 0)).await),
        ("users", "upsert") => ser(c.users().upsert(&text(a, 0), arg(a, 1)).await),
        ("users", "delete") => c.users().delete(&text(a, 0)).await.map(|_| Value::Null),
        ("users", "import") => ser(c.users().import(&arg::<Vec<likyly::UserImport>>(a, 0)).await),
        ("events", "track") => ser(c.events().track(&text(a, 0), arg(a, 1)).await),
        ("events", "trackMany") => ser(c.events().track_many(&arg::<Vec<likyly::TypedEvent>>(a, 0)).await),
        ("events", "impression") => ser(c.events().impression(arg(a, 0)).await),
        ("events", "view") => ser(c.events().view(arg(a, 0)).await),
        ("events", "click") => ser(c.events().click(arg(a, 0)).await),
        ("events", "addToCart") => ser(c.events().add_to_cart(arg(a, 0)).await),
        ("events", "removeFromCart") => ser(c.events().remove_from_cart(arg(a, 0)).await),
        ("events", "purchase") => ser(c.events().purchase(arg(a, 0)).await),
        ("recommendations", "get") => ser(c.recommendations().get(arg(a, 0)).await),
        ("recommendations", "popular") => ser(c.recommendations().popular(arg(a, 0)).await),
        ("recommendations", "similar") => ser(c.recommendations().similar(arg(a, 0)).await),
        ("recommendations", "collaborative") => ser(c.recommendations().collaborative(arg(a, 0)).await),
        ("recommendations", "hybrid") => ser(c.recommendations().hybrid(arg(a, 0)).await),
        ("recommendations", "session") => ser(c.recommendations().session(arg(a, 0)).await),
        other => panic!("scenario calls {other:?}, which the runner does not map"),
    }
}

fn pick(value: &Value, path: &str) -> Option<Value> {
    let mut cur = value.clone();
    let mut in_properties = false;
    for key in path.split('.') {
        cur = match &cur {
            Value::Array(items) if key == "length" => json!(items.len()),
            Value::Array(items) => items.get(key.parse::<usize>().ok()?)?.clone(),
            // `properties`: the developer's own keys, never renamed
            Value::Object(m) => m.get(&if in_properties { key.to_string() } else { snake(key) })?.clone(),
            _ => return None,
        };
        in_properties = key == "properties";
    }
    Some(cur)
}

fn class_of(e: &Error) -> &'static str {
    match e {
        Error::Validation(_) => "ValidationError",
        Error::Network(_) => "NetworkError",
        Error::Timeout(_) => "TimeoutError",
        Error::Authentication(_) => "AuthenticationError",
        Error::PermissionDenied(_) => "PermissionDeniedError",
        Error::NotFound(_) => "NotFoundError",
        Error::RateLimit(_) => "RateLimitError",
        Error::Api(_) => "ApiError",
        _ => "unknown",
    }
}

#[tokio::test]
async fn shared_scenarios() {
    let file: Value = serde_json::from_slice(&std::fs::read("../conformance/scenarios.json").unwrap()).unwrap();
    let defaults = &file["defaults"];
    let scenarios = file["scenarios"].as_array().unwrap();
    assert!(!scenarios.is_empty());
    let mut failures = Vec::new();

    for sc in scenarios {
        let id = sc["id"].as_str().unwrap();
        let mut step = Step {
            status: sc["response"]["status"].as_u64().unwrap() as u16,
            headers: sc["response"]["headers"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
                .collect(),
            ..Step::ok(sc["response"]["body"].clone())
        };
        step.body = Some(sc["response"]["body"].clone());
        let catalog = sc["config"]["catalog"].as_str().map(str::to_string);
        let h = harness_with(vec![step], |b| {
            let b = b.max_retries(0);
            match catalog {
                Some(c) => b.catalog(c),
                None => b,
            }
        });

        let call = &sc["call"];
        let result =
            invoke(&h.client, call["resource"].as_str().unwrap(), call["method"].as_str().unwrap(), call["args"].as_array().unwrap()).await;

        let mut problems: Vec<String> = Vec::new();
        match (&sc["error"], &result) {
            (Value::Null, Err(e)) => problems.push(format!("unexpected error: {e}")),
            (Value::Null, Ok(v)) => {
                for (path, want) in sc["expect"].as_object().into_iter().flatten() {
                    if pick(v, path).as_ref() != Some(want) {
                        problems.push(format!("result.{path} = {:?}, want {want}", pick(v, path)));
                    }
                }
            }
            (_, Ok(_)) => problems.push("expected an error".into()),
            (want, Err(e)) => {
                if class_of(e) != want["class"].as_str().unwrap() {
                    problems.push(format!("error class {} (want {}): {e}", class_of(e), want["class"]));
                }
                if e.status_code() != want["statusCode"].as_u64().map(|s| s as u16) {
                    problems.push(format!("status {:?}", e.status_code()));
                }
                if let Some(id) = want["requestId"].as_str() {
                    if e.request_id() != Some(id) {
                        problems.push(format!("request_id {:?}", e.request_id()));
                    }
                }
                if let Some(secs) = want["retryAfter"].as_f64() {
                    if e.retry_after().map(|d| d.as_secs_f64()) != Some(secs) {
                        problems.push(format!("retry_after {:?}", e.retry_after()));
                    }
                }
            }
        }

        let calls = h.transport.calls();
        if calls.len() != 1 {
            problems.push(format!("{} HTTP requests, want exactly 1", calls.len()));
        } else {
            let (got, want) = (&calls[0], &sc["request"]);
            let (path, query) = split_url(&got.url);
            if got.method != want["method"].as_str().unwrap() {
                problems.push(format!("method {}", got.method));
            }
            if !got.url.starts_with(defaults["baseUrl"].as_str().unwrap()) {
                problems.push(format!("origin of {}", got.url));
            }
            if path != want["path"].as_str().unwrap() {
                problems.push(format!("raw path {path}, want {}", want["path"]));
            }
            let want_query: std::collections::HashMap<String, String> =
                want["query"].as_object().into_iter().flatten().map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string())).collect();
            if query != want_query {
                problems.push(format!("query {query:?}, want {want_query:?}"));
            }
            if header(got, "X-API-Key") != defaults["apiKey"].as_str() {
                problems.push("X-API-Key".into());
            }
            if !header(got, "User-Agent").unwrap_or("").starts_with(defaults["userAgentPrefix"].as_str().unwrap()) {
                problems.push("User-Agent".into());
            }
            let has_body = want.get("body").is_some();
            if header(got, "Content-Type") != has_body.then_some("application/json") {
                problems.push(format!("Content-Type {:?}", header(got, "Content-Type")));
            }
            let got_body: Option<Value> = got.body.as_ref().map(|b| serde_json::from_slice(b).unwrap());
            if got_body.as_ref() != want.get("body") {
                problems.push(format!("body {got_body:?}, want {:?}", want.get("body")));
            }
        }
        if !problems.is_empty() {
            failures.push(format!("{id}: {}", problems.join("; ")));
        }
    }
    assert!(failures.is_empty(), "{} of {} scenarios failed:\n{}", failures.len(), scenarios.len(), failures.join("\n"));
    println!("{} scenarios passed", scenarios.len());
}
