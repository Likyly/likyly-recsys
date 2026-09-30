use serde_json::{json, Map, Value};

use crate::encoding::{encode, require_id, require_path_safe_id};
use crate::error::Error;
use crate::http::{Call, Core};
use crate::types::{
    CollaborativeOptions, HybridOptions, PopularOptions, RecommendationRequest, RecommendationResponse, SessionOptions, SimilarOptions,
};

const DEFAULT_LIMIT: u32 = 10;

/// `client.recommendations()`. Use [`get`](Recommendations::get): send what you know and LIKYLY picks the best
/// strategy. The other methods are the *Advanced Recommendations* - one strategy at a time.
pub struct Recommendations<'a> {
    core: &'a Core,
}

impl<'a> Recommendations<'a> {
    pub(crate) fn new(core: &'a Core) -> Self {
        Self { core }
    }

    /// Recommendations for a user, an anonymous session, an item being viewed, viewed items - or nothing at all
    /// (then you get what is popular). You never choose the algorithm; `strategy` in the response says what was
    /// used. Send `recommendation_id` back on the events that follow.
    pub async fn get(&self, request: RecommendationRequest) -> Result<RecommendationResponse, Error> {
        let mut body = Map::new();
        for (key, name, value) in [
            ("user_id", "user_id", &request.user_id),
            ("session_id", "session_id", &request.session_id),
            ("item_id", "item_id", &request.item_id),
        ] {
            if let Some(v) = value {
                body.insert(key.into(), json!(require_id(v, name)?));
            }
        }
        if let Some(ids) = &request.viewed_item_ids {
            let ids = ids.iter().map(|id| require_id(id, "viewed_item_ids[]")).collect::<Result<Vec<_>, _>>()?;
            body.insert("viewed_item_ids".into(), json!(ids));
        }
        if let Some(p) = &request.placement {
            body.insert("placement".into(), json!(p));
        }
        if let Some(limit) = request.limit {
            body.insert("count".into(), json!(limit));
        }
        if request.debug {
            body.insert("debug".into(), json!(true));
        }
        // A read: repeating it only mints another recommendation_id, so it is retried.
        let call = Call { method: "POST", path: "/getRec".into(), query: vec![], body: Some(Value::Object(body)), idempotent: true };
        self.core.request(call).await?.json()
    }

    // ---- Advanced Recommendations ------------------------------------------------------------------
    // One strategy at a time, for expert use. Ids travel in the URL path here, so they cannot contain "/".

    /// The most popular items - the fallback for a visitor with no history at all.
    pub async fn popular(&self, options: PopularOptions) -> Result<RecommendationResponse, Error> {
        let limit = limit_of(options.limit)?;
        self.advanced(format!("/getRec/popular/{limit}"), &options.placement, &options.session_id, vec![]).await
    }

    /// Items similar to one item (content similarity). No user needed.
    pub async fn similar(&self, options: SimilarOptions) -> Result<RecommendationResponse, Error> {
        let item = require_path_safe_id(&options.item_id, "item_id")?;
        let limit = limit_of(options.limit)?;
        self.advanced(format!("/getRec/content/{}/{limit}", encode(item)), &options.placement, &options.session_id, vec![]).await
    }

    /// Collaborative filtering: what users with similar histories liked. Needs a trained model.
    pub async fn collaborative(&self, options: CollaborativeOptions) -> Result<RecommendationResponse, Error> {
        let user = require_path_safe_id(&options.user_id, "user_id")?;
        let limit = limit_of(options.limit)?;
        self.advanced(format!("/getRec/collaborative/{}/{limit}", encode(user)), &options.placement, &options.session_id, vec![]).await
    }

    /// Similar items, personalized for a user. `alpha` (0-1) weighs the collaborative signal against content similarity.
    pub async fn hybrid(&self, options: HybridOptions) -> Result<RecommendationResponse, Error> {
        let user = require_path_safe_id(&options.user_id, "user_id")?;
        let item = require_path_safe_id(&options.item_id, "item_id")?;
        let limit = limit_of(options.limit)?;
        let extra = options.alpha.map(|a| vec![("alpha".to_string(), a.to_string())]).unwrap_or_default();
        let path = format!("/getRec/hybrid/{}/{}/{limit}", encode(user), encode(item));
        self.advanced(path, &options.placement, &options.session_id, extra).await
    }

    /// Recency-weighted recommendations from what was viewed: pass `viewed_item_ids` (an explicit list, oldest
    /// first) **or** `user_id` (LIKYLY's own history of that user's views) - exactly one.
    pub async fn session(&self, options: SessionOptions) -> Result<RecommendationResponse, Error> {
        if options.viewed_item_ids.is_some() == options.user_id.is_some() {
            return Err(Error::validation("session() needs either viewed_item_ids or user_id (exactly one)"));
        }
        let limit = limit_of(options.limit)?;
        if let Some(user) = &options.user_id {
            let user = require_path_safe_id(user, "user_id")?;
            return self
                .advanced(format!("/getRec/sessionForUser/{}/{limit}", encode(user)), &options.placement, &options.session_id, vec![])
                .await;
        }
        let ids = options.viewed_item_ids.as_deref().unwrap_or_default();
        if ids.is_empty() {
            return Err(Error::validation("viewed_item_ids must contain at least one item id"));
        }
        for id in ids {
            require_id(id, "viewed_item_ids[]")?;
            if id.contains(',') {
                return Err(Error::validation(
                    "an item id containing \",\" cannot be sent in this endpoint's comma-separated list - use recommendations().get()",
                ));
            }
        }
        let extra = vec![("viewed_item_ids".to_string(), ids.join(",")), ("count".to_string(), limit.to_string())];
        self.advanced("/getRec/session".into(), &options.placement, &options.session_id, extra).await
    }

    async fn advanced(
        &self,
        path: String,
        placement: &Option<String>,
        session_id: &Option<String>,
        extra: Vec<(String, String)>,
    ) -> Result<RecommendationResponse, Error> {
        // response_format=object: always the same {recommendation_id, strategy, items} envelope
        let mut query = vec![("response_format".to_string(), "object".to_string())];
        if let Some(p) = placement {
            query.push(("placement".into(), p.clone()));
        }
        if let Some(s) = session_id {
            query.push(("session_id".into(), s.clone()));
        }
        query.extend(extra);
        let call = Call { method: "GET", path, query, body: None, idempotent: true };
        self.core.request(call).await?.json()
    }
}

fn limit_of(limit: Option<u32>) -> Result<u32, Error> {
    match limit.unwrap_or(DEFAULT_LIMIT) {
        0 => Err(Error::validation("limit must be a positive integer")),
        n => Ok(n),
    }
}
