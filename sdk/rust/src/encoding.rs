use crate::error::Error;

/// Percent-encodes a path segment or query value: everything outside RFC 3986 "unreserved"
/// (`A-Z a-z 0-9 - . _ ~`) becomes `%XX` (UTF-8), `/` included - so an id like `gid://shopify/Product/1` is one segment.
pub(crate) fn encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for b in value.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// Ids are opaque, non-empty strings.
pub(crate) fn require_id<'a>(value: &'a str, name: &str) -> Result<&'a str, Error> {
    if value.trim().is_empty() {
        return Err(Error::validation(format!("{name} must be a non-empty string (your own identifier, e.g. \"SKU-123\")")));
    }
    Ok(value)
}

/// Advanced recommendation endpoints carry ids in the URL path, where `/` cannot be used.
pub(crate) fn require_path_safe_id<'a>(value: &'a str, name: &str) -> Result<&'a str, Error> {
    let id = require_id(value, name)?;
    if id.contains('/') {
        return Err(Error::validation(format!(
            "{name} \"{id}\" contains \"/\", which this endpoint cannot carry in its URL path - use recommendations().get(), which takes ids in the request body"
        )));
    }
    Ok(id)
}

pub(crate) fn require_event_type(value: &str) -> Result<&str, Error> {
    let ok = !value.is_empty() && value.len() <= 64 && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-');
    if ok {
        Ok(value)
    } else {
        Err(Error::validation(
            "event type must be 1-64 characters of letters, digits, \"_\" or \"-\" (e.g. \"view\", \"add_to_cart\", \"favorite\")",
        ))
    }
}
