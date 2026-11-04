use serde_json::{json, Map, Value};

use crate::encoding::{encode, require_id};
use crate::error::Error;
use crate::http::{Call, Core};
use crate::types::{PlacementContext, PlacementRecommendOptions, PlacementRecommendResponse};

/// `client.placements()` - the recommended way to ask for recommendations. A placement ("what to recommend, where,
/// with what fallback and filters") is configured once - with the LIKYLY MCP admin tools or the dashboard, never
/// from this SDK - and called by its slug from then on. [`recommendations()`](crate::Likyly::recommendations)
/// remains available for per-call use with no stored configuration.
pub struct Placements<'a> {
    core: &'a Core,
}

impl<'a> Placements<'a> {
    pub(crate) fn new(core: &'a Core) -> Self {
        Self { core }
    }

    /// Recommendations for one placement. Send whatever context you have (`item_id`, `user_id`, `session_id`, ...):
    /// the placement's own configuration decides the strategy, fallback and filters, not you. `strategy_used` in
    /// the response says what was used. Send `recommendation_id` back on the events that follow.
    ///
    /// ```no_run
    /// # async fn run(client: likyly::Likyly) -> Result<(), likyly::Error> {
    /// use likyly::{PlacementContext, PlacementRecommendOptions};
    ///
    /// let recs = client
    ///     .placements()
    ///     .recommend(PlacementRecommendOptions {
    ///         context: Some(PlacementContext { item_id: Some("SKU-456".into()), ..Default::default() }),
    ///         ..PlacementRecommendOptions::new("pdp-related")
    ///     })
    ///     .await?;
    /// # Ok(()) }
    /// ```
    pub async fn recommend(&self, options: PlacementRecommendOptions) -> Result<PlacementRecommendResponse, Error> {
        let placement = require_id(&options.placement, "placement")?;
        let mut body = Map::new();
        if let Some(context) = &options.context {
            body.insert("context".into(), Value::Object(context_body(context)?));
        }
        if let Some(limit) = options.limit {
            body.insert("limit".into(), json!(limit));
        }
        // A read in effect: repeating it only mints another recommendation_id, so it is retried.
        let call = Call {
            method: "POST",
            path: format!("/placements/{}/recommend", encode(placement)),
            query: vec![],
            body: Some(Value::Object(body)),
            idempotent: true,
        };
        self.core.request(call).await?.json()
    }
}

/// Only the fields you set are sent. `custom_context` is passed through untouched: its keys are yours.
fn context_body(c: &PlacementContext) -> Result<Map<String, Value>, Error> {
    let mut body = Map::new();
    for (key, name, value) in [
        ("current_item_id", "context.item_id", &c.item_id),
        ("category_id", "context.category_id", &c.category_id),
        ("collection_id", "context.collection_id", &c.collection_id),
        ("user_id", "context.user_id", &c.user_id),
        ("anonymous_id", "context.anonymous_id", &c.anonymous_id),
        ("session_id", "context.session_id", &c.session_id),
    ] {
        if let Some(v) = value {
            body.insert(key.into(), json!(require_id(v, name)?));
        }
    }
    if let Some(ids) = &c.cart_item_ids {
        let ids = ids.iter().map(|id| require_id(id, "context.cart_item_ids[]")).collect::<Result<Vec<_>, _>>()?;
        body.insert("cart_item_ids".into(), json!(ids));
    }
    if let Some(locale) = &c.locale {
        body.insert("locale".into(), json!(locale));
    }
    if let Some(custom) = &c.custom_context {
        body.insert("custom_context".into(), Value::Object(custom.clone()));
    }
    Ok(body)
}
