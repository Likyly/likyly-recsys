//! End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless LIKYLY_TEST_URL,
//! LIKYLY_TEST_SECRET_KEY and LIKYLY_TEST_PUBLIC_KEY are set.

use likyly::*;
use serde_json::{json, Value};

const GID: &str = "gid://shopify/Product/123456";

struct Live {
    url: String,
    secret: String,
    public: String,
    catalog: String,
}

impl Live {
    fn from_env() -> Option<Live> {
        let get = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis();
        Some(Live {
            url: get("LIKYLY_TEST_URL")?,
            secret: get("LIKYLY_TEST_SECRET_KEY")?,
            public: get("LIKYLY_TEST_PUBLIC_KEY")?,
            catalog: format!("rust-{stamp}"),
        })
    }
    fn client(&self, key: &str) -> Likyly {
        Likyly::builder(key).base_url(&self.url).catalog(&self.catalog).build().unwrap()
    }
    fn server(&self) -> Likyly {
        self.client(&self.secret)
    }
    fn browser(&self) -> Likyly {
        self.client(&self.public)
    }
}

fn props(v: Value) -> Properties {
    v.as_object().unwrap().clone()
}

#[tokio::test]
async fn live_api() {
    let Some(live) = Live::from_env() else {
        eprintln!("LIKYLY_TEST_* not set - skipping");
        return;
    };

    // items: upsert / get / list / delete, with ids of any shape
    let c = live.server();
    let created = c
        .items()
        .upsert(
            "SKU-123",
            ItemInput {
                title: "Nike Air Max".into(),
                description: Some("Running shoe".into()),
                properties: Some(props(json!({ "category": "shoes", "brand": "Nike", "price": 129.9 }))),
            },
        )
        .await
        .unwrap();
    assert_eq!(created.item_id, "SKU-123");
    assert_eq!(created.properties, props(json!({ "category": "shoes", "brand": "Nike", "price": 129.9 })));
    let item = |title: &str, description: &str, category: &str| ItemInput {
        title: title.into(),
        description: Some(description.into()),
        properties: Some(props(json!({ "category": category }))),
    };
    c.items().upsert(GID, item("Shopify boot", "warm winter boot", "boots")).await.unwrap();
    c.items().upsert("3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", item("UUID shoe", "trail running shoe", "shoes")).await.unwrap();
    assert_eq!(c.items().get(GID).await.unwrap().title, "Shopify boot");

    let page = c.items().list(ListOptions { limit: Some(2), offset: None }).await.unwrap();
    assert_eq!((page.items.len(), page.total), (2, Some(3)));

    let import = |id: &str, title: &str| ItemImport {
        item_id: id.into(),
        title: title.into(),
        description: Some("road running shoe".into()),
        properties: Some(props(json!({ "category": "shoes" }))),
    };
    let many = c.items().upsert_many(&[import("SKU-A", "Adidas"), import("SKU-B", "Asics")]).await.unwrap();
    assert_eq!(many.succeeded, 2);

    c.items().delete("SKU-B").await.unwrap();
    assert!(matches!(c.items().get("SKU-B").await, Err(Error::NotFound(_))));
    let gone = c.items().delete_many(&["SKU-A", "ghost"]).await.unwrap();
    assert_eq!((gone.succeeded, gone.failed, gone.errors[0].id.as_deref()), (1, 1, Some("ghost")));
    c.items().upsert("SKU-A", item("Adidas", "road running shoe", "shoes")).await.unwrap();

    // users: upsert / get / list / delete with free-form properties
    let user = c
        .users()
        .upsert("user_123", UserInput { properties: Some(props(json!({ "country": "FR", "segment": "premium", "language": "fr" }))) })
        .await
        .unwrap();
    assert_eq!(user.user_id, "user_123");
    assert_eq!(c.users().get("user_123").await.unwrap().properties["segment"], "premium");
    assert!(c.users().list(ListOptions { limit: Some(10), offset: None }).await.unwrap().users.iter().any(|u| u.user_id == "user_123"));
    let imported =
        c.users().import(&[UserImport { user_id: "u_imp".into(), properties: Some(props(json!({ "country": "DE" }))) }]).await.unwrap();
    assert_eq!(imported.succeeded, 1);
    c.users().delete("u_imp").await.unwrap();
    assert!(matches!(c.users().get("u_imp").await, Err(Error::NotFound(_))));

    // events: identified, anonymous, both, custom, idempotent purchase, batch - with the PUBLIC key
    let b = live.browser();
    b.events().view(EventInput::for_user("user_123", "SKU-123")).await.unwrap();
    b.events().view(EventInput::for_session("sess_123", "SKU-123")).await.unwrap();
    b.events().view(EventInput { session_id: Some("sess_123".into()), ..EventInput::for_user("user_789", GID) }).await.unwrap();
    b.events()
        .track(
            "favorite",
            EventInput { properties: Some(props(json!({ "source": "wishlist" }))), ..EventInput::for_user("user_123", "SKU-123") },
        )
        .await
        .unwrap();
    b.events().add_to_cart(EventInput { quantity: Some(1), ..EventInput::for_user("user_123", "SKU-123") }).await.unwrap();
    b.events().remove_from_cart(EventInput { quantity: Some(1), ..EventInput::for_user("user_123", "SKU-123") }).await.unwrap();
    let purchase = EventInput {
        event_id: Some(format!("purchase_{}", live.catalog)),
        quantity: Some(1),
        properties: Some(props(json!({ "price": 129.9, "currency": "EUR", "orderId": "ORDER-9281" }))),
        ..EventInput::for_user("user_123", "SKU-123")
    };
    assert!(!b.events().purchase(purchase.clone()).await.unwrap().duplicate);
    assert!(b.events().purchase(purchase).await.unwrap().duplicate);
    let batch = b
        .events()
        .track_many(&[
            TypedEvent::new("view", EventInput::for_user("user_123", "SKU-A")),
            TypedEvent::new("click", EventInput::for_session("sess_123", "SKU-A")),
        ])
        .await
        .unwrap();
    assert_eq!(batch, EventBatchResult { received: 2, accepted: 2, duplicates: 0 });

    // recommendations: get in every context, then attribution through recommendation_id
    let r = b.recommendations();
    let for_user = r
        .get(RecommendationRequest {
            user_id: Some("user_123".into()),
            placement: Some("homepage".into()),
            limit: Some(3),
            ..Default::default()
        })
        .await
        .unwrap();
    let id = &for_user.recommendation_id;
    assert!(id.len() == 30 && id.starts_with("rec_") && id[4..].chars().all(|c| c.is_ascii_digit() || c.is_ascii_uppercase()), "{id}");
    assert_eq!(for_user.placement.as_deref(), Some("homepage"));
    assert!((1..=3).contains(&for_user.items.len()));
    let item_req = |item: &str| RecommendationRequest { item_id: Some(item.into()), limit: Some(2), ..Default::default() };
    assert_eq!(r.get(item_req("SKU-123")).await.unwrap().strategy, "content");
    let session_req = RecommendationRequest {
        session_id: Some("sess_123".into()),
        viewed_item_ids: Some(vec!["SKU-123".into()]),
        limit: Some(2),
        ..Default::default()
    };
    assert_eq!(r.get(session_req).await.unwrap().strategy, "session");
    assert_eq!(r.get(item_req(GID)).await.unwrap().strategy, "content"); // an id with "/" in the body
    assert!(!r.get(RecommendationRequest { limit: Some(2), ..Default::default() }).await.unwrap().recommendation_id.is_empty());

    let shown = &for_user.items[0];
    let attributed = EventInput {
        recommendation_id: Some(for_user.recommendation_id.clone()),
        placement: Some("homepage".into()),
        ..EventInput::for_user("user_123", shown.item_id.clone())
    };
    b.events().impression(attributed.clone()).await.unwrap();
    assert!(!b.events().click(attributed).await.unwrap().duplicate);

    // advanced recommendations answer with the same response type
    assert!(!r.popular(PopularOptions { limit: Some(2), ..Default::default() }).await.unwrap().recommendation_id.is_empty());
    let similar = r.similar(SimilarOptions { item_id: "SKU-123".into(), limit: Some(2), ..Default::default() }).await.unwrap();
    assert_eq!(similar.strategy, "content");
    assert!(!similar.items[0].item_id.is_empty());
    assert!(!r
        .hybrid(HybridOptions {
            user_id: "user_123".into(),
            item_id: "SKU-123".into(),
            alpha: Some(0.3),
            limit: Some(2),
            ..Default::default()
        })
        .await
        .unwrap()
        .recommendation_id
        .is_empty());
    let by_list = r
        .session(SessionOptions { viewed_item_ids: Some(vec!["SKU-123".into(), "SKU-A".into()]), limit: Some(2), ..Default::default() })
        .await
        .unwrap();
    assert_eq!(by_list.strategy, "session");
    assert!(!r
        .session(SessionOptions { user_id: Some("user_123".into()), limit: Some(2), ..Default::default() })
        .await
        .unwrap()
        .recommendation_id
        .is_empty());
    assert!(matches!(
        r.collaborative(CollaborativeOptions { user_id: "user_123".into(), limit: Some(2), ..Default::default() }).await,
        Err(Error::NotFound(_))
    )); // no trained model yet

    // the public key cannot manage the catalog or users; a wrong key is refused
    let page_one = ItemInput { title: "t".into(), ..Default::default() };
    assert!(matches!(b.items().upsert("x", page_one).await, Err(Error::PermissionDenied(_))));
    assert!(matches!(b.items().list(Default::default()).await, Err(Error::PermissionDenied(_))));
    assert!(matches!(b.users().get("user_123").await, Err(Error::PermissionDenied(_))));
    let bad = Likyly::builder("nope").base_url(&live.url).build().unwrap();
    assert!(matches!(bad.events().view(EventInput::for_user("u", "i")).await, Err(Error::Authentication(_))));

    // API-side validation surfaces as Validation with the request id
    let err = c.items().list(ListOptions { limit: Some(5000), offset: None }).await.unwrap_err();
    assert!(matches!(err, Error::Validation(ValidationError { api: Some(_), .. })), "{err:?}");
    assert_eq!(err.status_code(), Some(422));
    assert!(err.request_id().unwrap().starts_with("req_"));

    // a real timeout, through the real transport: nothing listens on this address
    let slow =
        Likyly::builder("k").base_url("http://10.255.255.1").timeout(std::time::Duration::from_millis(200)).max_retries(0).build().unwrap();
    assert!(matches!(slow.items().get("a").await, Err(Error::Timeout(_)) | Err(Error::Network(_))));

    // cleanup
    c.items().delete_many(&["SKU-123", GID, "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "SKU-A"]).await.unwrap();
    c.users().delete("user_123").await.unwrap();
}
