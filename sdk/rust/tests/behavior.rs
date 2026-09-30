mod common;

use std::sync::Arc;
use std::time::Duration;

use common::*;
use likyly::*;
use serde_json::{json, Map, Value};

fn props(v: Value) -> Properties {
    v.as_object().unwrap().clone()
}

fn is_validation<T: std::fmt::Debug>(r: Result<T, Error>) -> bool {
    matches!(r, Err(Error::Validation(ValidationError { api: None, .. })))
}

// ---- configuration ------------------------------------------------------------------------------

#[test]
fn requires_an_api_key() {
    for key in ["", "   "] {
        assert!(matches!(Likyly::new(key), Err(Error::Validation(_))), "{key:?}");
    }
}

#[tokio::test]
async fn defaults_to_production_and_strips_trailing_slashes() {
    let transport = Arc::new(FakeTransportHandle::new());
    let client = Likyly::builder("k").transport(transport.clone()).build().unwrap();
    client.items().list(Default::default()).await.unwrap();
    assert!(transport.last_url().starts_with("https://api.likyly.com/items"));

    let transport = Arc::new(FakeTransportHandle::new());
    let client = Likyly::builder("k").base_url("https://proxy.example.test/likyly///").transport(transport.clone()).build().unwrap();
    client.items().list(Default::default()).await.unwrap();
    assert_eq!(transport.last_url(), "https://proxy.example.test/likyly/items");
}

/// A tiny transport for tests that do not need scripting: always answers `[]`.
struct FakeTransportHandle(std::sync::Mutex<Vec<String>>);
impl FakeTransportHandle {
    fn new() -> Self {
        Self(Default::default())
    }
    fn last_url(&self) -> String {
        self.0.lock().unwrap().last().cloned().unwrap()
    }
}
impl Transport for FakeTransportHandle {
    fn send(&self, request: HttpRequest) -> TransportFuture<'_> {
        self.0.lock().unwrap().push(request.url);
        Box::pin(async { Ok(HttpResponse { status: 200, headers: Default::default(), body: b"[]".to_vec() }) })
    }
}

#[tokio::test]
async fn user_agent_is_extended_not_replaced() {
    let h = harness_with(vec![Step::ok(json!([]))], |b| b.user_agent("my-shop/1.4"));
    h.client.items().list(Default::default()).await.unwrap();
    let ua = header(&h.call(0), "User-Agent").unwrap().to_string();
    assert!(ua.starts_with("likyly-rust/1.") && ua.ends_with(" my-shop/1.4"), "{ua}");
}

#[tokio::test]
async fn the_api_key_goes_in_the_header_never_the_url_nor_debug_output() {
    let h = harness(vec![Step::ok(json!([]))]);
    h.client.items().list(Default::default()).await.unwrap();
    let call = h.call(0);
    assert_eq!(header(&call, "X-API-Key"), Some(API_KEY));
    assert!(!call.url.contains("sk_test"));
    assert!(!format!("{:?}", h.client).contains("sk_test"));
}

#[tokio::test]
async fn with_options_overrides_timeout_and_retries_for_that_client_only() {
    let h = harness_with(vec![Step::down()], |b| b.timeout(Duration::from_secs(3)).max_retries(5));
    let quick = h.client.with_options(RequestOptions { timeout: Some(Duration::from_millis(1500)), max_retries: Some(0) });
    assert!(quick.items().get("a").await.is_err());
    assert_eq!(h.transport.count(), 1);
    assert_eq!(h.call(0).timeout, Duration::from_millis(1500));
    let _ = h.client.items().get("a").await; // the original keeps its own settings: 1 + 5 retries
    assert_eq!(h.transport.count(), 1 + 6);
    assert_eq!(h.call(1).timeout, Duration::from_secs(3));
}

// ---- validation: nothing is sent ------------------------------------------------------------------

#[tokio::test]
async fn validation_happens_before_anything_is_sent() {
    let h = harness(vec![Step::ok(evt())]);
    let c = &h.client;
    let ev = |item: &str, user: Option<&str>, session: Option<&str>| EventInput {
        item_id: item.into(),
        user_id: user.map(Into::into),
        session_id: session.map(Into::into),
        ..Default::default()
    };

    assert!(is_validation(c.events().view(ev("a", None, None)).await), "event without a user or a session");
    assert!(is_validation(c.events().view(ev("", Some("u"), None)).await), "event without an item");
    assert!(is_validation(c.events().view(ev("  ", Some("u"), None)).await), "blank item id");
    assert!(is_validation(c.events().view(ev("a", Some(""), None)).await), "blank user id");
    assert!(is_validation(c.items().get("").await), "blank id on get");
    assert!(is_validation(c.items().upsert("a", ItemInput::default()).await), "item without a title");
    assert!(is_validation(c.items().upsert_many(&[]).await), "empty batch");
    assert!(is_validation(c.items().delete_many::<&str>(&[]).await), "empty delete batch");
    assert!(is_validation(c.users().import(&[]).await), "empty user batch");
    assert!(is_validation(c.events().track_many(&[]).await), "empty event batch");
    assert!(is_validation(c.events().track("bad type!", ev("i", Some("u"), None)).await), "bad event type");
    assert!(is_validation(c.events().track(&"x".repeat(65), ev("i", Some("u"), None)).await), "event type too long");
    let rec = c.recommendations();
    assert!(is_validation(rec.popular(PopularOptions { limit: Some(0), ..Default::default() }).await), "zero limit");
    assert!(is_validation(rec.session(SessionOptions::default()).await), "session with nothing");
    assert!(
        is_validation(
            rec.session(SessionOptions { viewed_item_ids: Some(vec!["a".into()]), user_id: Some("u".into()), ..Default::default() }).await
        ),
        "session with both"
    );
    assert!(
        is_validation(rec.session(SessionOptions { viewed_item_ids: Some(vec![]), ..Default::default() }).await),
        "session with an empty list"
    );
    assert!(
        is_validation(rec.session(SessionOptions { viewed_item_ids: Some(vec!["a,b".into()]), ..Default::default() }).await),
        "session id with a comma"
    );
    assert_eq!(h.transport.count(), 0, "nothing may be sent");
}

#[tokio::test]
async fn event_types_are_open_strings_but_url_safe() {
    let h = harness(vec![Step::ok(evt())]);
    h.client.events().track("favorite", EventInput::for_user("u", "i")).await.unwrap();
    assert_eq!(split_url(&h.call(0).url).0, "/events/favorite");
}

#[tokio::test]
async fn advanced_endpoints_refuse_an_id_containing_a_slash() {
    let h = harness(vec![Step::ok(rec())]);
    let err = h
        .client
        .recommendations()
        .similar(SimilarOptions { item_id: "gid://shopify/Product/1".into(), ..Default::default() })
        .await
        .unwrap_err();
    assert!(err.to_string().contains("recommendations().get()"), "{err}");
    // the body-based call accepts it
    h.client
        .recommendations()
        .get(RecommendationRequest { item_id: Some("gid://shopify/Product/1".into()), ..Default::default() })
        .await
        .unwrap();
    assert_eq!(h.transport.count(), 1);
}

#[tokio::test]
async fn occurred_at_is_sent_as_given() {
    let h = harness(vec![Step::ok(evt())]);
    let event = EventInput { occurred_at: Some("2026-09-24T10:30:00Z".into()), ..EventInput::for_user("u", "i") };
    h.client.events().view(event).await.unwrap();
    assert_eq!(h.body(0)["occurred_at"], "2026-09-24T10:30:00Z");
}

#[tokio::test]
async fn properties_keys_are_never_renamed_at_any_depth() {
    let h = harness(vec![Step::ok(evt())]);
    let p = props(json!({ "orderId": "O-1", "nested": { "snakeCase_and_camelCase": 1 } }));
    h.client.events().purchase(EventInput { properties: Some(p.clone()), ..EventInput::for_user("u", "i") }).await.unwrap();
    assert_eq!(h.body(0)["properties"], Value::Object(p));
}

#[tokio::test]
async fn ids_are_encoded_as_a_single_segment() {
    let h = harness(vec![Step::ok(item())]);
    h.client.items().get("a b/c?d#e%f é").await.unwrap();
    assert_eq!(split_url(&h.call(0).url).0, "/items/a%20b%2Fc%3Fd%23e%25f%20%C3%A9");
}

#[tokio::test]
async fn alpha_zero_is_sent_not_dropped() {
    let h = harness(vec![Step::ok(rec())]);
    h.client
        .recommendations()
        .hybrid(HybridOptions { user_id: "u".into(), item_id: "i".into(), alpha: Some(0.0), ..Default::default() })
        .await
        .unwrap();
    assert_eq!(split_url(&h.call(0).url).1["alpha"], "0");
}

#[tokio::test]
async fn debug_and_session_are_sent_on_get_and_an_empty_get_sends_an_empty_object() {
    let h = harness(vec![Step::ok(rec())]);
    h.client
        .recommendations()
        .get(RecommendationRequest { session_id: Some("s".into()), debug: true, limit: Some(3), ..Default::default() })
        .await
        .unwrap();
    assert_eq!(h.body(0), json!({ "session_id": "s", "count": 3, "debug": true }));
    h.client.recommendations().get(Default::default()).await.unwrap();
    assert_eq!(h.body(1), json!({}));
}

// ---- errors -----------------------------------------------------------------------------------------

#[tokio::test]
async fn errors_expose_status_request_id_and_body() {
    let h = harness_with(vec![Step::status(401, json!({ "detail": "nope", "request_id": "req_x" }))], |b| b.max_retries(0));
    let err = h.client.items().get("a").await.unwrap_err();
    assert!(matches!(err, Error::Authentication(_)));
    assert_eq!((err.status_code(), err.request_id()), (Some(401), Some("req_x")));
    assert_eq!(err.body().unwrap()["detail"], "nope");
    assert!(err.to_string().contains("req_x"));
    let _: &dyn std::error::Error = &err; // usable with `?` into Box<dyn Error>
}

#[tokio::test]
async fn each_status_maps_to_its_variant() {
    for (status, ok) in [
        (403u16, (|e: &Error| matches!(e, Error::PermissionDenied(_))) as fn(&Error) -> bool),
        (404, |e| matches!(e, Error::NotFound(_))),
        (422, |e| matches!(e, Error::Validation(ValidationError { api: Some(_), .. }))),
        (429, |e| matches!(e, Error::RateLimit(_))),
        (500, |e| matches!(e, Error::Api(_))),
    ] {
        let h = harness_with(vec![Step::status(status, json!({ "detail": "x" }))], |b| b.max_retries(0));
        let err = h.client.items().get("a").await.unwrap_err();
        assert!(ok(&err), "status {status} -> {err:?}");
        assert_eq!(err.status_code(), Some(status));
    }
}

#[tokio::test]
async fn a_422_lists_the_offending_fields_in_the_message() {
    let body = json!({ "detail": [{ "loc": ["body", "title"], "msg": "Field required" }], "request_id": "r" });
    let h = harness_with(vec![Step::status(422, body)], |b| b.max_retries(0));
    assert!(h.client.items().get("a").await.unwrap_err().to_string().contains("Field required"));
}

#[tokio::test]
async fn a_non_json_error_body_still_becomes_an_api_error() {
    let h = harness_with(vec![Step::raw(502, "<html>Bad gateway</html>")], |b| b.max_retries(0));
    let err = h.client.items().get("a").await.unwrap_err();
    assert!(matches!(err, Error::Api(_)));
    assert_eq!(err.status_code(), Some(502));
    assert_eq!(err.body(), Some(&json!("<html>Bad gateway</html>")));
}

#[tokio::test]
async fn a_non_json_success_body_is_an_api_error() {
    let h = harness_with(vec![Step::raw(200, "<html>captive portal</html>")], |b| b.max_retries(0));
    assert!(matches!(h.client.items().get("a").await, Err(Error::Api(_))));
}

#[tokio::test]
async fn transport_failures_become_network_and_timeout_errors() {
    let h = harness_with(vec![Step::fail(TransportError::Network("connection reset".into()))], |b| b.max_retries(0));
    let err = h.client.items().get("a").await.unwrap_err();
    assert!(matches!(&err, Error::Network(n) if n.message == "connection reset"));
    let h = harness_with(vec![Step::timeout()], |b| b.max_retries(0).timeout(Duration::from_secs(2)));
    assert!(matches!(h.client.items().get("a").await, Err(Error::Timeout(TimeoutError { timeout })) if timeout == Duration::from_secs(2)));
}

// ---- retries ----------------------------------------------------------------------------------------

#[tokio::test]
async fn a_429_is_retried_for_every_request_honoring_retry_after() {
    let h = harness(vec![Step::status(429, json!({ "detail": "slow down" })).header("Retry-After", "3"), Step::ok(evt())]);
    let result = h.client.events().view(EventInput::for_user("u", "i")).await.unwrap(); // no event_id: still safe, a 429 never reached the app
    assert!(!result.duplicate);
    assert_eq!(h.transport.count(), 2);
    assert_eq!(h.sleeps(), vec![Duration::from_secs(3)]);
}

#[tokio::test]
async fn a_retry_after_of_zero_means_retry_now_and_none_means_backoff() {
    let h = harness(vec![Step::status(429, json!({ "detail": "x" })).header("Retry-After", "0"), Step::ok(item())]);
    h.client.items().get("a").await.unwrap();
    assert_eq!(h.sleeps(), vec![Duration::ZERO]);

    let h = harness(vec![Step::status(429, json!({ "detail": "x" })), Step::ok(item())]);
    h.client.items().get("a").await.unwrap();
    assert_eq!(h.sleeps(), vec![Duration::from_millis(500)]);
}

#[tokio::test]
async fn a_retry_after_beyond_a_minute_is_not_waited_for() {
    let h = harness(vec![Step::status(429, json!({ "detail": "x" })).header("Retry-After", "600")]);
    let err = h.client.items().get("a").await.unwrap_err();
    assert!(matches!(err, Error::RateLimit(_)));
    assert_eq!(err.retry_after(), Some(Duration::from_secs(600)));
    assert_eq!(h.transport.count(), 1);
}

#[tokio::test]
async fn retry_after_accepts_an_http_date() {
    let date = httpdate_in(30);
    let h = harness_with(vec![Step::status(429, json!({ "detail": "x" })).header("Retry-After", &date)], |b| b.max_retries(0));
    let secs = h.client.items().get("a").await.unwrap_err().retry_after().unwrap().as_secs_f64();
    assert!((28.0..=31.0).contains(&secs), "{secs}");
}

fn httpdate_in(seconds: u64) -> String {
    // IMF-fixdate without a date crate: format through the same parser's inverse
    let when = std::time::SystemTime::now() + Duration::from_secs(seconds);
    let secs = when.duration_since(std::time::UNIX_EPOCH).unwrap().as_secs() as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let (h, m, s) = (rem / 3600, rem % 3600 / 60, rem % 60);
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(mo <= 2);
    let wd = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"][days.rem_euclid(7) as usize];
    let month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][(mo - 1) as usize];
    format!("{wd}, {d:02} {month} {y} {h:02}:{m:02}:{s:02} GMT")
}

#[tokio::test]
async fn exponential_backoff_with_jitter_then_gives_up() {
    let h = harness_with(vec![Step::down()], |b| b.max_retries(3));
    assert!(matches!(h.client.items().get("a").await, Err(Error::Api(_))));
    assert_eq!(h.transport.count(), 4);
    assert_eq!(h.sleeps(), vec![Duration::from_millis(500), Duration::from_secs(1), Duration::from_secs(2)]);
    // random pinned to 1
}

#[tokio::test]
async fn backoff_is_capped_at_eight_seconds() {
    let h = harness_with(vec![Step::down()], |b| b.max_retries(7));
    let _ = h.client.items().get("a").await;
    assert_eq!(h.sleeps().last(), Some(&Duration::from_secs(8)));
}

#[tokio::test]
async fn idempotent_calls_are_retried_on_502_503_504_with_the_same_body() {
    for status in [502u16, 503, 504] {
        let h = harness(vec![Step::status(status, json!({ "detail": "x" })), Step::ok(item())]);
        h.client.items().upsert("a", ItemInput { title: "t".into(), ..Default::default() }).await.unwrap();
        assert_eq!(h.transport.count(), 2, "status {status}");
        assert_eq!(h.call(0).body, h.call(1).body, "the replay must carry the same body");
    }
}

#[tokio::test]
async fn an_event_without_an_event_id_is_never_retried_on_an_ambiguous_failure() {
    for first in [Step::down(), Step::fail(TransportError::Network("reset".into())), Step::timeout()] {
        let h = harness(vec![first, Step::ok(evt())]);
        assert!(h.client.events().purchase(EventInput::for_user("u", "i")).await.is_err());
        assert_eq!(h.transport.count(), 1, "a duplicate purchase could have been recorded");
    }
}

#[tokio::test]
async fn an_event_with_an_event_id_is_retried() {
    let h = harness(vec![Step::down(), Step::ok(json!({ "message": "ok", "event_id": "e1", "duplicate": true }))]);
    let result = h.client.events().purchase(EventInput { event_id: Some("e1".into()), ..EventInput::for_user("u", "i") }).await.unwrap();
    assert_eq!(h.transport.count(), 2);
    assert!(result.duplicate);
    assert_eq!(result.event_id.as_deref(), Some("e1"));
}

#[tokio::test]
async fn track_many_is_retried_only_if_every_event_has_an_event_id() {
    let batch = json!({ "received": 2, "accepted": 2, "duplicates": 0 });
    let keyed = |id: &str, item: &str| TypedEvent::new("view", EventInput { event_id: Some(id.into()), ..EventInput::for_user("u", item) });

    let a = harness(vec![Step::down(), Step::ok(batch.clone())]);
    let events = [TypedEvent::new("view", EventInput::for_user("u", "1")), keyed("e", "2")];
    assert!(a.client.events().track_many(&events).await.is_err());
    assert_eq!(a.transport.count(), 1);

    let b = harness(vec![Step::down(), Step::ok(batch)]);
    b.client.events().track_many(&[keyed("e1", "1"), keyed("e2", "2")]).await.unwrap();
    assert_eq!(b.transport.count(), 2);
}

#[tokio::test]
async fn client_errors_are_never_retried() {
    for status in [400u16, 401, 403, 404, 422] {
        let h = harness(vec![Step::status(status, json!({ "detail": "x" })), Step::ok(json!({}))]);
        assert!(h.client.items().get("a").await.is_err());
        assert_eq!(h.transport.count(), 1, "status {status}");
    }
}

// ---- lists ------------------------------------------------------------------------------------------

#[tokio::test]
async fn list_reports_the_total_and_the_pagination_used() {
    let h = harness(vec![Step::ok(json!([{ "item_id": "a", "title": "A" }])).header("X-Total-Count", "42")]);
    let page = h.client.items().list(ListOptions { limit: Some(1), offset: Some(10) }).await.unwrap();
    assert_eq!((page.total, page.limit, page.offset, page.items.len()), (Some(42), Some(1), 10, 1));
    let (_, query) = split_url(&h.call(0).url);
    assert_eq!(query.len(), 2);
}

#[tokio::test]
async fn list_sends_only_what_you_asked_for_and_total_is_none_when_unreported() {
    let h = harness(vec![Step::ok(json!([]))]);
    let page = h.client.users().list(Default::default()).await.unwrap();
    assert!(split_url(&h.call(0).url).1.is_empty());
    assert_eq!((page.total, page.limit, page.offset, page.users.len()), (None, None, 0, 0));
}

#[tokio::test]
async fn missing_optional_response_fields_are_none_and_properties_default_to_empty() {
    let h = harness(vec![Step::ok(
        json!({ "recommendation_id": "r", "strategy": "popular", "items": [{ "item_id": "a", "properties": null }] }),
    )]);
    let response = h.client.recommendations().get(Default::default()).await.unwrap();
    let item = &response.items[0];
    assert_eq!(item.item_id, "a");
    assert!(item.score.is_none() && item.explanation.is_none() && item.properties == Map::new());
}

#[tokio::test]
async fn dropping_the_future_cancels_the_call_without_retrying() {
    // Rust's cancellation model: dropping the future stops it - no SDK error, no retry.
    let h = harness(vec![Step { hang: true, ..Step::ok(json!({})) }]);
    let items = h.client.items();
    let outcome = tokio::time::timeout(Duration::from_millis(50), items.get("a")).await;
    assert!(outcome.is_err(), "the call should still be pending when the caller gives up");
    assert_eq!(h.transport.count(), 1);
    assert!(h.sleeps().is_empty());
}
