use serde_json::{json, Map, Value};

use crate::encoding::{encode, require_id};
use crate::error::Error;
use crate::http::{Call, Core, Response};
use crate::types::{BatchResult, Item, ItemImport, ItemInput, ItemList, ListOptions, Properties};

/// `client.items()` - your catalog. Needs the **secret** API key.
pub struct Items<'a> {
    core: &'a Core,
}

impl<'a> Items<'a> {
    pub(crate) fn new(core: &'a Core) -> Self {
        Self { core }
    }

    /// One item by your own id.
    pub async fn get(&self, item_id: &str) -> Result<Item, Error> {
        let id = require_id(item_id, "item_id")?;
        let call = Call { method: "GET", path: format!("/items/{}", encode(id)), query: vec![], body: None, idempotent: true };
        self.core.request(call).await?.json()
    }

    /// One page of the catalog. Pagination is `limit` + `offset` (the API's default is 100 per page); `total` is
    /// the whole catalog's size.
    pub async fn list(&self, page: ListOptions) -> Result<ItemList, Error> {
        let call = Call { method: "GET", path: "/items".into(), query: page_query(page), body: None, idempotent: true };
        let response = self.core.request(call).await?;
        Ok(ItemList { items: response.json()?, total: total_of(&response), limit: page.limit, offset: page.offset.unwrap_or(0) })
    }

    /// Creates the item, or replaces it if it exists - idempotent, safe to call as often as you like. The body is
    /// the whole item: fields you leave out are cleared.
    pub async fn upsert(&self, item_id: &str, item: ItemInput) -> Result<Item, Error> {
        let id = require_id(item_id, "item_id")?;
        let body = item_body(&item.title, item.description, item.properties)?;
        let call = Call {
            method: "PUT",
            path: format!("/items/{}", encode(id)),
            query: vec![],
            body: Some(Value::Object(body)),
            idempotent: true,
        };
        self.core.request(call).await?.json()
    }

    /// Removes the item from the catalog. Events already recorded for it are kept.
    pub async fn delete(&self, item_id: &str) -> Result<(), Error> {
        let id = require_id(item_id, "item_id")?;
        let call = Call { method: "DELETE", path: format!("/items/{}", encode(id)), query: vec![], body: None, idempotent: true };
        self.core.request(call).await.map(|_| ())
    }

    /// Batch upsert (1-1000 items) - each entry behaves like [`upsert`](Items::upsert). A failing entry is
    /// reported in [`BatchResult::errors`].
    pub async fn upsert_many(&self, items: &[ItemImport]) -> Result<BatchResult, Error> {
        if items.is_empty() {
            return Err(Error::validation("items must not be empty"));
        }
        let mut entries = Vec::with_capacity(items.len());
        for i in items {
            let id = require_id(&i.item_id, "item_id")?;
            let mut body = item_body(&i.title, i.description.clone(), i.properties.clone())?;
            body.insert("item_id".into(), json!(id));
            entries.push(Value::Object(body));
        }
        // An upsert: replaying it changes nothing, so it is retried.
        let call =
            Call { method: "POST", path: "/items/import".into(), query: vec![], body: Some(json!({ "items": entries })), idempotent: true };
        self.core.request(call).await?.json()
    }

    /// Alias of [`upsert_many`](Items::upsert_many): the API's `POST /items/import` is a JSON batch upsert.
    /// (A CSV import exists only in the LIKYLY dashboard.)
    pub async fn import(&self, items: &[ItemImport]) -> Result<BatchResult, Error> {
        self.upsert_many(items).await
    }

    /// Batch delete (1-1000 ids). Ids that don't exist are reported in [`BatchResult::errors`].
    pub async fn delete_many<S: AsRef<str>>(&self, item_ids: &[S]) -> Result<BatchResult, Error> {
        if item_ids.is_empty() {
            return Err(Error::validation("item_ids must not be empty"));
        }
        let ids = item_ids.iter().map(|id| require_id(id.as_ref(), "item_id")).collect::<Result<Vec<_>, _>>()?;
        let call =
            Call { method: "POST", path: "/items/delete".into(), query: vec![], body: Some(json!({ "item_ids": ids })), idempotent: true };
        self.core.request(call).await?.json()
    }
}

fn item_body(title: &str, description: Option<String>, properties: Option<Properties>) -> Result<Map<String, Value>, Error> {
    if title.is_empty() {
        return Err(Error::validation("an item needs a non-empty title"));
    }
    let mut body = Map::new();
    body.insert("title".into(), json!(title));
    if let Some(d) = description {
        body.insert("description".into(), json!(d));
    }
    if let Some(p) = properties {
        body.insert("properties".into(), Value::Object(p));
    }
    Ok(body)
}

pub(crate) fn page_query(page: ListOptions) -> Vec<(String, String)> {
    let mut query = Vec::new();
    if let Some(limit) = page.limit {
        query.push(("limit".to_string(), limit.to_string()));
    }
    if let Some(offset) = page.offset {
        query.push(("offset".to_string(), offset.to_string()));
    }
    query
}

pub(crate) fn total_of(response: &Response) -> Option<u64> {
    response.headers.get("x-total-count")?.parse().ok()
}
