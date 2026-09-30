use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{Map, Value};

/// Free-form JSON object: price, currency, orderId, category, any field of your own. Keys are stored exactly as
/// you send them and never renamed by the SDK.
pub type Properties = Map<String, Value>;

fn null_default<'de, D, T>(d: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

// ---- Items ---------------------------------------------------------------------------------------

/// A catalog entry.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Item {
    /// Your own identifier - any string (`SKU-123`, a UUID, `gid://shopify/Product/123`).
    pub item_id: String,
    pub title: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default, deserialize_with = "null_default")]
    pub properties: Properties,
}

/// What you send to create or replace an item. Only `title` is required.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ItemInput {
    pub title: String,
    #[serde(default)]
    pub description: Option<String>,
    /// `category` and `description` feed content similarity; everything else is stored as is.
    #[serde(default)]
    pub properties: Option<Properties>,
}

/// An item with its id, for `items().upsert_many`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ItemImport {
    pub item_id: String,
    pub title: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub properties: Option<Properties>,
}

/// Pagination: `None` means "the API's default".
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ListOptions {
    /// Page size (1-1000; the API's default for items is 100, users are all returned).
    #[serde(default)]
    pub limit: Option<u32>,
    /// Rows to skip.
    #[serde(default)]
    pub offset: Option<u32>,
}

/// One page of the catalog.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ItemList {
    pub items: Vec<Item>,
    /// The catalog's whole size (`X-Total-Count`); `None` if the API did not report it.
    pub total: Option<u64>,
    /// The page size you asked for (`None` = the API's default).
    pub limit: Option<u32>,
    pub offset: u32,
}

// ---- Users ---------------------------------------------------------------------------------------

/// An optional user profile.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct User {
    pub user_id: String,
    #[serde(default, deserialize_with = "null_default")]
    pub properties: Properties,
}

/// What you send to create or replace a user.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct UserInput {
    /// country, segment, language, ... - whatever describes your users.
    #[serde(default)]
    pub properties: Option<Properties>,
}

/// A user with its id, for `users().import`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct UserImport {
    pub user_id: String,
    #[serde(default)]
    pub properties: Option<Properties>,
}

/// One page of users.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UserList {
    pub users: Vec<User>,
    pub total: Option<u64>,
    pub limit: Option<u32>,
    pub offset: u32,
}

/// One failed entry of a batch call.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatchError {
    pub index: u32,
    #[serde(default)]
    pub id: Option<String>,
    pub message: String,
}

/// Result of a batch call. A failing entry is reported in `errors`, it does not return an `Err`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatchResult {
    pub received: u32,
    pub succeeded: u32,
    pub failed: u32,
    #[serde(default, deserialize_with = "null_default")]
    pub errors: Vec<BatchError>,
}

// ---- Events --------------------------------------------------------------------------------------

/// An interaction. `item_id` and at least one of `user_id` / `session_id` are required; sending both ties an
/// anonymous session to the user.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct EventInput {
    pub item_id: String,
    #[serde(default)]
    pub user_id: Option<String>,
    /// Anonymous visitor / browsing session - no login needed.
    #[serde(default)]
    pub session_id: Option<String>,
    /// The `recommendation_id` of the recommendation that surfaced this item - enables attribution.
    #[serde(default)]
    pub recommendation_id: Option<String>,
    /// Free-form label of where this happened: `homepage`, `product_page`, `cart`, ...
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub quantity: Option<u32>,
    /// ISO 8601 / RFC 3339 timestamp (`"2026-09-24T10:30:00Z"`). Defaults to the server's time.
    #[serde(default)]
    pub occurred_at: Option<String>,
    #[serde(default)]
    pub properties: Option<Properties>,
    /// Idempotency key, unique per account: replaying an event with the same `event_id` records nothing and
    /// returns `duplicate: true`. Set it on purchases - it makes retries safe.
    #[serde(default)]
    pub event_id: Option<String>,
}

impl EventInput {
    /// An event by a known user.
    pub fn for_user(user_id: impl Into<String>, item_id: impl Into<String>) -> Self {
        Self { user_id: Some(user_id.into()), item_id: item_id.into(), ..Default::default() }
    }

    /// An event by an anonymous session.
    pub fn for_session(session_id: impl Into<String>, item_id: impl Into<String>) -> Self {
        Self { session_id: Some(session_id.into()), item_id: item_id.into(), ..Default::default() }
    }
}

/// An event of any type, for `events().track_many`. The type is an open string (`view`, `favorite`, ...).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct TypedEvent {
    #[serde(rename = "type")]
    pub event_type: String,
    #[serde(flatten)]
    pub event: EventInput,
}

impl TypedEvent {
    pub fn new(event_type: impl Into<String>, event: EventInput) -> Self {
        Self { event_type: event_type.into(), event }
    }
}

/// The answer to a recorded event.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EventResult {
    pub message: String,
    #[serde(default)]
    pub event_id: Option<String>,
    /// True if this `event_id` was already recorded - nothing was written.
    #[serde(default)]
    pub duplicate: bool,
}

/// The answer to `events().track_many`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EventBatchResult {
    pub received: u32,
    pub accepted: u32,
    /// Events skipped because their `event_id` was already recorded.
    pub duplicates: u32,
}

// ---- Recommendations -----------------------------------------------------------------------------

/// Send whatever you know: LIKYLY chooses the best strategy. Every field is optional.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RecommendationRequest {
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    /// The item being looked at (e.g. the product page).
    #[serde(default)]
    pub item_id: Option<String>,
    /// Recently viewed items, oldest first.
    #[serde(default)]
    pub viewed_item_ids: Option<Vec<String>>,
    /// Free-form label of where the recommendations will be shown.
    #[serde(default)]
    pub placement: Option<String>,
    /// How many items to return (1-100, default 10).
    #[serde(default)]
    pub limit: Option<u32>,
    /// Diagnostic detail in `explanation`. Secret key only.
    #[serde(default)]
    pub debug: bool,
}

/// A user whose history contributed to a recommendation (`debug`, secret key only).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SimilarUser {
    pub user_id: String,
    #[serde(default, deserialize_with = "null_default")]
    pub shared_item_ids: Vec<String>,
}

/// Why an item was recommended.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Explanation {
    pub reason: String,
    #[serde(default)]
    pub content_similarity: Option<f64>,
    #[serde(default)]
    pub semantic_similarity: Option<f64>,
    #[serde(default)]
    pub popularity_score: Option<f64>,
    #[serde(default)]
    pub interaction_count: Option<u64>,
    #[serde(default)]
    pub interaction_label: Option<String>,
    #[serde(default)]
    pub collaborative_score: Option<f64>,
    #[serde(default)]
    pub source_item_ids: Option<Vec<String>>,
    /// Only with `debug` and a secret key.
    #[serde(default)]
    pub similar_users: Option<Vec<SimilarUser>>,
}

/// One recommended item.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RecommendedItem {
    pub item_id: String,
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default, deserialize_with = "null_default")]
    pub properties: Properties,
    #[serde(default)]
    pub explanation: Option<Explanation>,
}

/// What every recommendation call returns.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RecommendationResponse {
    /// Send it back on the impression / click / add_to_cart / purchase events for these items.
    pub recommendation_id: String,
    /// What LIKYLY used: `hybrid`, `content`, `collaborative`, `session` or `popular`.
    pub strategy: String,
    #[serde(default)]
    pub placement: Option<String>,
    pub items: Vec<RecommendedItem>,
}

// The Advanced Recommendations' options share `placement`, `session_id` (attaches the recommendation to an
// anonymous session, for attribution) and `limit` (default 10).

/// The most popular items.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct PopularOptions {
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

/// Items similar to `item_id`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SimilarOptions {
    pub item_id: String,
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

/// What users with similar histories to `user_id` liked.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct CollaborativeOptions {
    pub user_id: String,
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

/// Similar items, personalized for a user.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct HybridOptions {
    pub user_id: String,
    pub item_id: String,
    /// Weight of the collaborative signal against content similarity: 0 = pure content, 1 = pure collaborative.
    /// `None` = the API's default (0.5).
    #[serde(default)]
    pub alpha: Option<f64>,
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

/// Give `viewed_item_ids` (an explicit list, oldest first) OR `user_id` (LIKYLY's own history of that user's
/// views) - exactly one.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SessionOptions {
    #[serde(default)]
    pub viewed_item_ids: Option<Vec<String>>,
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}
