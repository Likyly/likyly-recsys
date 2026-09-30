package com.likyly.model;

import java.util.Map;

/**
 * A catalog item.
 *
 * @param itemId your own identifier - any string (SKU-123, a UUID, gid://shopify/Product/123)
 * @param title 
 * @param description may be null
 * @param properties free-form: category, price, brand, ...
 */
public record Item(
    String itemId,
    String title,
    String description,
    Map<String, Object> properties) {
}
