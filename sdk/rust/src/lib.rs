//! Official Rust client for the [LIKYLY](https://likyly.com) recommendations API.
//!
//! ```no_run
//! use likyly::{EventInput, Likyly, RecommendationRequest};
//!
//! # async fn run() -> Result<(), likyly::Error> {
//! let client = Likyly::new(std::env::var("LIKYLY_SECRET_KEY").unwrap())?;
//!
//! // Track what a visitor does
//! client.events().view(EventInput::for_user("user_123", "SKU-456")).await?;
//!
//! // Ask what to show them
//! let recs = client
//!     .recommendations()
//!     .get(RecommendationRequest { user_id: Some("user_123".into()), placement: Some("homepage".into()), limit: Some(8), ..Default::default() })
//!     .await?;
//! for item in &recs.items {
//!     println!("{}", item.item_id);
//! }
//! # Ok(()) }
//! ```
//!
//! There are two kinds of API key. The **secret** key is for your backend only (items, users, everything). The
//! **public** key is safe to expose in a web page but can only ask for recommendations and record events. Never
//! ship the secret key in code that runs on a visitor's device.
//!
//! Errors are values of [`Error`]; transient failures (429, 502, 503, 504, dropped connections) are retried
//! automatically - see [`LikylyBuilder::max_retries`].

mod client;
mod encoding;
mod error;
mod events;
mod http;
mod items;
mod recommendations;
mod types;
mod users;

pub use client::{Likyly, LikylyBuilder, RequestOptions, DEFAULT_BASE_URL, VERSION};
pub use error::{ApiError, Error, NetworkError, TimeoutError, ValidationError};
pub use events::Events;
pub use http::{HttpRequest, HttpResponse, ReqwestTransport, Transport, TransportError, TransportFuture};
pub use items::Items;
pub use recommendations::Recommendations;
pub use types::*;
pub use users::Users;
