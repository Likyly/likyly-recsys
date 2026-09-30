//! Quick start: catalog -> events -> recommendations -> attribution.
//!
//! Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... cargo run --example quickstart

// region:imports
use std::time::Duration;

use likyly::{
    Error, EventInput, HybridOptions, ItemImport, Likyly, Properties, RecommendationRequest, SessionOptions, SimilarOptions, TypedEvent,
    UserInput,
};
use serde_json::json;
// endregion

fn props(v: serde_json::Value) -> Properties {
    v.as_object().cloned().unwrap_or_default()
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    // region:initialize
    // Backend only: the secret key gives access to your catalog and your users.
    let likyly = Likyly::builder(std::env::var("LIKYLY_SECRET_KEY").unwrap())
        .base_url(std::env::var("LIKYLY_BASE_URL").unwrap_or_else(|_| likyly::DEFAULT_BASE_URL.into())) // docs:omit
        .catalog(std::env::var("LIKYLY_CATALOG").unwrap_or_default()) // docs:omit
        .build()?;
    // endregion

    // region:catalog
    // Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
    let shoe = |id: &str, title: &str, description: &str, brand: &str, price: f64| ItemImport {
        item_id: id.into(),
        title: title.into(),
        description: Some(description.into()),
        properties: Some(props(json!({ "category": "shoes", "brand": brand, "price": price }))),
    };
    likyly
        .items()
        .upsert_many(&[
            shoe("SKU-1", "Nike Air Max", "Running shoe with air cushioning", "Nike", 129.9),
            shoe("SKU-2", "Adidas Ultraboost", "Responsive running shoe", "Adidas", 149.0),
            shoe("SKU-3", "Nike Pegasus", "Everyday running shoe", "Nike", 119.0),
        ])
        .await?;

    // Users are optional: describe them if you want the profile to travel with their events.
    likyly.users().upsert("user_123", UserInput { properties: Some(props(json!({ "country": "FR", "segment": "premium" }))) }).await?;
    // endregion

    // region:track
    // Tell LIKYLY what your visitors do.
    likyly.events().view(EventInput::for_user("user_123", "SKU-1")).await?;

    // Purchases carry an event_id: replaying the call can never count the sale twice.
    likyly
        .events()
        .purchase(EventInput {
            event_id: Some("purchase_order_9281_SKU-1".into()),
            quantity: Some(1),
            properties: Some(props(json!({ "price": 129.9, "currency": "EUR", "orderId": "order_9281" }))),
            ..EventInput::for_user("user_123", "SKU-1")
        })
        .await?;
    // endregion

    // region:recommend
    // Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
    let recs = likyly
        .recommendations()
        .get(RecommendationRequest {
            user_id: Some("user_123".into()),
            placement: Some("homepage".into()),
            limit: Some(3),
            ..Default::default()
        })
        .await?;

    println!("strategy: {}", recs.strategy);
    for item in &recs.items {
        println!("{} {}", item.item_id, item.title.as_deref().unwrap_or(""));
    }
    // endregion

    // region:showcase
    // Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
    let product_recs = likyly
        .recommendations()
        .get(RecommendationRequest {
            user_id: Some("user_123".into()),       // visiteur connecté
            session_id: Some("sess_abc".into()),    // ou visiteur anonyme (cookie)
            item_id: Some("SKU-1".into()),          // fiche produit en cours de consultation
            placement: Some("product_page".into()), // où elles seront affichées (libre)
            limit: Some(6),                         // combien d'articles (10 par défaut)
            ..Default::default()
        })
        .await?;

    for item in &product_recs.items {
        println!("{} {:?} {:?}", item.item_id, item.title, item.score);
    }
    // endregion

    // region:attribution
    // Send the recommendation_id back: LIKYLY measures which recommendations get seen, clicked and bought.
    let shown = EventInput {
        recommendation_id: Some(recs.recommendation_id.clone()),
        placement: Some("homepage".into()),
        ..EventInput::for_user("user_123", recs.items[0].item_id.clone())
    };
    likyly.events().impression(shown.clone()).await?;
    likyly.events().click(shown).await?;
    // endregion

    // region:browser
    // Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
    let front = Likyly::builder(std::env::var("LIKYLY_PUBLIC_KEY").unwrap())
        .base_url(std::env::var("LIKYLY_BASE_URL").unwrap_or_else(|_| likyly::DEFAULT_BASE_URL.into())) // docs:omit
        .catalog(std::env::var("LIKYLY_CATALOG").unwrap_or_default()) // docs:omit
        .build()?;

    // A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
    front.events().view(EventInput::for_session("sess_abc", "SKU-2")).await?;
    let for_visitor = front
        .recommendations()
        .get(RecommendationRequest {
            session_id: Some("sess_abc".into()),
            viewed_item_ids: Some(vec!["SKU-2".into()]),
            placement: Some("product_page".into()),
            limit: Some(3),
            ..Default::default()
        })
        .await?;
    // endregion

    // region:custom
    // Any string is a valid event type: track what matters to your business.
    likyly
        .events()
        .track(
            "favorite",
            EventInput { properties: Some(props(json!({ "list": "wishlist" }))), ..EventInput::for_user("user_123", "SKU-2") },
        )
        .await?;

    // Up to 1000 events per call, each with its own type.
    likyly
        .events()
        .track_many(&[
            TypedEvent::new("view", EventInput::for_user("user_123", "SKU-3")),
            TypedEvent::new("add_to_cart", EventInput { quantity: Some(1), ..EventInput::for_user("user_123", "SKU-3") }),
        ])
        .await?;
    // endregion

    // region:advanced
    // Advanced Recommendations: one strategy at a time, when you want to choose.
    let similar =
        likyly.recommendations().similar(SimilarOptions { item_id: "SKU-1".into(), limit: Some(3), ..Default::default() }).await?;
    let hybrid = likyly
        .recommendations()
        .hybrid(HybridOptions {
            user_id: "user_123".into(),
            item_id: "SKU-1".into(),
            alpha: Some(0.7),
            limit: Some(3),
            ..Default::default()
        })
        .await?;
    let session = likyly
        .recommendations()
        .session(SessionOptions { viewed_item_ids: Some(vec!["SKU-1".into(), "SKU-2".into()]), limit: Some(3), ..Default::default() })
        .await?;
    // endregion
    let _ = (similar, hybrid, session);

    // region:config
    let tuned = Likyly::builder(std::env::var("LIKYLY_SECRET_KEY").unwrap())
        .timeout(Duration::from_secs(5)) // per attempt (default 10 s)
        .max_retries(3) // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
        .user_agent("my-shop/1.4") // appended to the SDK's User-Agent
        .base_url(std::env::var("LIKYLY_BASE_URL").unwrap_or_else(|_| likyly::DEFAULT_BASE_URL.into())) // docs:omit
        .build()?;
    // endregion
    let _ = tuned;

    // region:errors
    match likyly.items().get("does-not-exist").await {
        Ok(_) => {}
        Err(Error::NotFound(e)) => println!("no such item, request {:?}", e.request_id),
        Err(Error::RateLimit(e)) => println!("slow down, retry in {:?}", e.retry_after),
        Err(other) => return Err(other),
    }
    // endregion

    println!("visitor strategy: {}", for_visitor.strategy);

    // region:cleanup
    likyly.items().delete_many(&["SKU-1", "SKU-2", "SKU-3"]).await?;
    likyly.users().delete("user_123").await?;
    // endregion
    println!("quickstart ok");
    Ok(())
}
