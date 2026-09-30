use std::sync::Arc;
use std::time::Duration;

use crate::error::Error;
use crate::events::Events;
use crate::http::{default_random, default_sleeper, Core, Random, ReqwestTransport, Sleeper, Transport};
use crate::items::Items;
use crate::recommendations::Recommendations;
use crate::users::Users;

/// The production API.
pub const DEFAULT_BASE_URL: &str = "https://api.likyly.com";

/// The SDK version, sent in the `User-Agent` header.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Per-call overrides: see [`Likyly::with_options`].
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RequestOptions {
    /// Overrides the client's per-attempt timeout.
    pub timeout: Option<Duration>,
    /// Overrides the client's retry count (`Some(0)` disables retries).
    pub max_retries: Option<u32>,
}

/// The LIKYLY client. Four things to know:
///
/// * [`items()`](Likyly::items) - your catalog
/// * [`users()`](Likyly::users) - your users (optional)
/// * [`events()`](Likyly::events) - what visitors do
/// * [`recommendations()`](Likyly::recommendations) - what to show them
///
/// Cloning is cheap and shares the connection pool: create one client and reuse it.
#[derive(Clone)]
pub struct Likyly {
    core: Core,
}

impl Likyly {
    /// A client with the default settings. `api_key` is one of your two keys: the **secret** key (backend only:
    /// items, users, everything) or the **public** key (safe in a web page: recommendations and event tracking
    /// only). Never put the secret key in code that ships to a browser.
    pub fn new(api_key: impl Into<String>) -> Result<Self, Error> {
        Self::builder(api_key).build()
    }

    /// Configure a client: base URL, catalog, timeout, retries, User-Agent, transport.
    pub fn builder(api_key: impl Into<String>) -> LikylyBuilder {
        LikylyBuilder::new(api_key.into())
    }

    /// Your catalog. Needs the secret API key.
    pub fn items(&self) -> Items<'_> {
        Items::new(&self.core)
    }

    /// Your optional user profiles. Needs the secret API key.
    pub fn users(&self) -> Users<'_> {
        Users::new(&self.core)
    }

    /// What your visitors do. Works with the public key from a browser, or the secret key from your backend.
    pub fn events(&self) -> Events<'_> {
        Events::new(&self.core)
    }

    /// What to show them.
    pub fn recommendations(&self) -> Recommendations<'_> {
        Recommendations::new(&self.core)
    }

    /// A copy of this client with a different timeout and/or retry count, for a single call or a group of calls:
    ///
    /// ```no_run
    /// # async fn run(client: likyly::Likyly) -> Result<(), likyly::Error> {
    /// use likyly::RequestOptions;
    /// use std::time::Duration;
    ///
    /// let quick = client.with_options(RequestOptions { timeout: Some(Duration::from_millis(800)), max_retries: Some(0) });
    /// let recs = quick.recommendations().get(Default::default()).await?;
    /// # Ok(()) }
    /// ```
    pub fn with_options(&self, options: RequestOptions) -> Likyly {
        let mut core = self.core.clone();
        if let Some(t) = options.timeout {
            core.timeout = t;
        }
        if let Some(n) = options.max_retries {
            core.max_retries = n;
        }
        Likyly { core }
    }
}

impl std::fmt::Debug for Likyly {
    // The API key is never printed.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Likyly").field("base_url", &self.core.base_url).field("catalog", &self.core.catalog).finish_non_exhaustive()
    }
}

/// Builder for [`Likyly`].
pub struct LikylyBuilder {
    api_key: String,
    base_url: String,
    catalog: Option<String>,
    timeout: Duration,
    max_retries: u32,
    user_agent: Option<String>,
    transport: Option<Arc<dyn Transport>>,
    sleeper: Option<Sleeper>,
    random: Option<Random>,
}

impl LikylyBuilder {
    fn new(api_key: String) -> Self {
        Self {
            api_key,
            base_url: DEFAULT_BASE_URL.to_string(),
            catalog: None,
            timeout: Duration::from_secs(10),
            max_retries: 2,
            user_agent: None,
            transport: None,
            sleeper: None,
            random: None,
        }
    }

    /// Defaults to `https://api.likyly.com`.
    pub fn base_url(mut self, url: impl Into<String>) -> Self {
        self.base_url = url.into();
        self
    }

    /// Which of your catalogs to use, if your account has several. Omit it if you have one: LIKYLY uses your only catalog.
    pub fn catalog(mut self, name: impl Into<String>) -> Self {
        self.catalog = Some(name.into()).filter(|n| !n.is_empty());
        self
    }

    /// Per-attempt request timeout. Default 10 s.
    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    /// Automatic retries on transient failures (429, 502, 503, 504, dropped connections). Default 2. `0` disables.
    pub fn max_retries(mut self, n: u32) -> Self {
        self.max_retries = n;
        self
    }

    /// Appended to the SDK's User-Agent, e.g. `my-shop/1.4`.
    pub fn user_agent(mut self, suffix: impl Into<String>) -> Self {
        self.user_agent = Some(suffix.into());
        self
    }

    /// Replaces the HTTP layer (see [`Transport`]).
    pub fn transport(mut self, transport: Arc<dyn Transport>) -> Self {
        self.transport = Some(transport);
        self
    }

    /// Test hooks: backoff without waiting, deterministic jitter.
    #[doc(hidden)]
    pub fn testing_hooks(mut self, sleeper: Sleeper, random: Random) -> Self {
        self.sleeper = Some(sleeper);
        self.random = Some(random);
        self
    }

    /// Validates the configuration and builds the client.
    pub fn build(self) -> Result<Likyly, Error> {
        if self.api_key.trim().is_empty() {
            return Err(Error::validation("api_key is required (create one in your LIKYLY account)"));
        }
        let mut user_agent = format!("likyly-rust/{VERSION}");
        if let Some(suffix) = &self.user_agent {
            user_agent.push(' ');
            user_agent.push_str(suffix);
        }
        let transport = self.transport.unwrap_or_else(|| Arc::new(ReqwestTransport::default()));
        Ok(Likyly {
            core: Core {
                transport,
                api_key: self.api_key.into(),
                base_url: self.base_url.trim_end_matches('/').into(),
                catalog: self.catalog.map(Into::into),
                timeout: self.timeout,
                max_retries: self.max_retries,
                user_agent: user_agent.into(),
                sleeper: self.sleeper.unwrap_or_else(default_sleeper),
                random: self.random.unwrap_or_else(default_random),
            },
        })
    }
}
