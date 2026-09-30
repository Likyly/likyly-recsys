use serde_json::{json, Map, Value};

use crate::encoding::{encode, require_id};
use crate::error::Error;
use crate::http::{Call, Core};
use crate::items::{page_query, total_of};
use crate::types::{BatchResult, ListOptions, User, UserImport, UserInput, UserList};

/// `client.users()` - optional user profiles. Needs the **secret** API key (profiles are personal data). You
/// don't have to create a user before sending events for them.
pub struct Users<'a> {
    core: &'a Core,
}

impl<'a> Users<'a> {
    pub(crate) fn new(core: &'a Core) -> Self {
        Self { core }
    }

    /// One user by your own id.
    pub async fn get(&self, user_id: &str) -> Result<User, Error> {
        let id = require_id(user_id, "user_id")?;
        let call = Call { method: "GET", path: format!("/users/{}", encode(id)), query: vec![], body: None, idempotent: true };
        self.core.request(call).await?.json()
    }

    /// One page of users. Pagination is `limit` + `offset`; without a `limit` the API returns every user.
    pub async fn list(&self, page: ListOptions) -> Result<UserList, Error> {
        let call = Call { method: "GET", path: "/users".into(), query: page_query(page), body: None, idempotent: true };
        let response = self.core.request(call).await?;
        Ok(UserList { users: response.json()?, total: total_of(&response), limit: page.limit, offset: page.offset.unwrap_or(0) })
    }

    /// Creates or replaces the profile (idempotent). `properties` is free-form: country, segment, language, ...
    pub async fn upsert(&self, user_id: &str, user: UserInput) -> Result<User, Error> {
        let id = require_id(user_id, "user_id")?;
        let mut body = Map::new();
        if let Some(p) = user.properties {
            body.insert("properties".into(), Value::Object(p));
        }
        let call = Call {
            method: "PUT",
            path: format!("/users/{}", encode(id)),
            query: vec![],
            body: Some(Value::Object(body)),
            idempotent: true,
        };
        self.core.request(call).await?.json()
    }

    /// Erases the user: the profile **and every event recorded for them**.
    pub async fn delete(&self, user_id: &str) -> Result<(), Error> {
        let id = require_id(user_id, "user_id")?;
        let call = Call { method: "DELETE", path: format!("/users/{}", encode(id)), query: vec![], body: None, idempotent: true };
        self.core.request(call).await.map(|_| ())
    }

    /// Batch upsert (1-1000 users). A failing entry is reported in [`BatchResult::errors`].
    pub async fn import(&self, users: &[UserImport]) -> Result<BatchResult, Error> {
        if users.is_empty() {
            return Err(Error::validation("users must not be empty"));
        }
        let mut entries = Vec::with_capacity(users.len());
        for u in users {
            let mut entry = Map::new();
            entry.insert("user_id".into(), json!(require_id(&u.user_id, "user_id")?));
            if let Some(p) = &u.properties {
                entry.insert("properties".into(), Value::Object(p.clone()));
            }
            entries.push(Value::Object(entry));
        }
        let call =
            Call { method: "POST", path: "/users/import".into(), query: vec![], body: Some(json!({ "users": entries })), idempotent: true };
        self.core.request(call).await?.json()
    }
}
