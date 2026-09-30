use serde_json::{json, Map, Value};

use crate::encoding::{encode, require_event_type, require_id};
use crate::error::Error;
use crate::http::{Call, Core};
use crate::types::{EventBatchResult, EventInput, EventResult, TypedEvent};

/// `client.events()` - what your visitors do. Works with the **public** key from a browser, or the secret key
/// from your backend. [`track`](Events::track) is the one mechanism; [`view`](Events::view),
/// [`click`](Events::click), ... are shortcuts that call it with the matching event type.
pub struct Events<'a> {
    core: &'a Core,
}

impl<'a> Events<'a> {
    pub(crate) fn new(core: &'a Core) -> Self {
        Self { core }
    }

    /// Records one event of any type: `"view"`, `"click"`, ... or your own (`"favorite"`, `"share"`, ...). Event
    /// types are open strings - the six helpers are conveniences, not a closed list.
    ///
    /// Retries: a failed call is only retried automatically when it carries an `event_id` (then a replay is
    /// harmless); without one, an ambiguous failure is returned rather than risking a duplicate.
    pub async fn track(&self, event_type: &str, event: EventInput) -> Result<EventResult, Error> {
        let event_type = require_event_type(event_type)?;
        let idempotent = event.event_id.is_some();
        let body = event_body(&event)?;
        let call = Call {
            method: "POST",
            path: format!("/events/{}", encode(event_type)),
            query: vec![],
            body: Some(Value::Object(body)),
            idempotent,
        };
        self.core.request(call).await?.json()
    }

    /// Up to 1000 events in one call, each with its own type. Validation is all-or-nothing.
    pub async fn track_many(&self, events: &[TypedEvent]) -> Result<EventBatchResult, Error> {
        if events.is_empty() {
            return Err(Error::validation("events must not be empty"));
        }
        let mut entries = Vec::with_capacity(events.len());
        for e in events {
            let mut body = event_body(&e.event)?;
            body.insert("event_type".into(), json!(require_event_type(&e.event_type)?));
            entries.push(Value::Object(body));
        }
        let idempotent = events.iter().all(|e| e.event.event_id.is_some());
        let call =
            Call { method: "POST", path: "/events/batch".into(), query: vec![], body: Some(json!({ "events": entries })), idempotent };
        self.core.request(call).await?.json()
    }

    /// The item was shown to the visitor (send the `recommendation_id` it came with).
    pub async fn impression(&self, event: EventInput) -> Result<EventResult, Error> {
        self.track("impression", event).await
    }

    /// The visitor looked at the item (a product page, an article).
    pub async fn view(&self, event: EventInput) -> Result<EventResult, Error> {
        self.track("view", event).await
    }

    /// The visitor clicked the item.
    pub async fn click(&self, event: EventInput) -> Result<EventResult, Error> {
        self.track("click", event).await
    }

    /// The visitor added the item to their cart.
    pub async fn add_to_cart(&self, event: EventInput) -> Result<EventResult, Error> {
        self.track("add_to_cart", event).await
    }

    /// The visitor removed the item from their cart.
    pub async fn remove_from_cart(&self, event: EventInput) -> Result<EventResult, Error> {
        self.track("remove_from_cart", event).await
    }

    /// The visitor bought the item. Set `event_id` (e.g. `purchase_<order>_<item>`) so a retry can never count
    /// the purchase twice.
    pub async fn purchase(&self, event: EventInput) -> Result<EventResult, Error> {
        self.track("purchase", event).await
    }
}

/// `properties` is passed through untouched: its keys are yours.
fn event_body(e: &EventInput) -> Result<Map<String, Value>, Error> {
    let item_id = require_id(&e.item_id, "item_id")?;
    if e.user_id.is_none() && e.session_id.is_none() {
        return Err(Error::validation("an event needs a user_id or a session_id (or both)"));
    }
    let mut body = Map::new();
    body.insert("item_id".into(), json!(item_id));
    if let Some(v) = &e.user_id {
        body.insert("user_id".into(), json!(require_id(v, "user_id")?));
    }
    if let Some(v) = &e.session_id {
        body.insert("session_id".into(), json!(require_id(v, "session_id")?));
    }
    for (key, value) in [
        ("event_id", &e.event_id),
        ("recommendation_id", &e.recommendation_id),
        ("placement", &e.placement),
        ("occurred_at", &e.occurred_at),
    ] {
        if let Some(v) = value {
            body.insert(key.into(), json!(v));
        }
    }
    if let Some(q) = e.quantity {
        body.insert("quantity".into(), json!(q));
    }
    if let Some(p) = &e.properties {
        body.insert("properties".into(), Value::Object(p.clone()));
    }
    Ok(body)
}
