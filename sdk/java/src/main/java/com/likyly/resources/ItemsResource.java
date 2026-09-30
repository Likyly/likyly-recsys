package com.likyly.resources;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.likyly.error.ValidationException;
import com.likyly.internal.HttpCore;
import com.likyly.internal.Support;
import com.likyly.internal.Wire;
import com.likyly.model.BatchResult;
import com.likyly.model.Item;
import com.likyly.model.ItemImport;
import com.likyly.model.ItemInput;
import com.likyly.model.ItemList;
import com.likyly.model.ListOptions;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** {@code likyly.items()} - your catalog. Needs the <b>secret</b> API key. */
public final class ItemsResource {
    private final HttpCore http;

    public ItemsResource(HttpCore http) {
        this.http = http;
    }

    /** One item by your own id. */
    public Item get(String itemId) {
        String path = "/items/" + HttpCore.encode(Support.requireId(itemId, "itemId"));
        return Wire.item(http.request("GET", path, Map.of(), null, true).data());
    }

    /** The first page, with the API's default page size. */
    public ItemList list() {
        return list(new ListOptions(null, null));
    }

    /** One page of the catalog. Pagination is {@code limit} + {@code offset}; {@code total} is the whole catalog's size. */
    public ItemList list(ListOptions options) {
        Map<String, String> query = new LinkedHashMap<>();
        if (options.limit() != null) {
            query.put("limit", String.valueOf(options.limit()));
        }
        if (options.offset() != null) {
            query.put("offset", String.valueOf(options.offset()));
        }
        HttpCore.Result r = http.request("GET", "/items", query, null, true);
        List<Item> items = new ArrayList<>();
        r.data().forEach(n -> items.add(Wire.item(n)));
        String total = r.headers().get("x-total-count");
        return new ItemList(items, total == null ? null : Long.valueOf(total), options.limit(), options.offset() == null ? 0 : options.offset());
    }

    /** Creates the item, or replaces it if it exists - idempotent. Fields you leave out are cleared. */
    public Item upsert(String itemId, ItemInput item) {
        String path = "/items/" + HttpCore.encode(Support.requireId(itemId, "itemId"));
        return Wire.item(http.request("PUT", path, Map.of(), body(item.title(), item.description(), item.properties()), true).data());
    }

    /** Removes the item from the catalog. Events already recorded for it are kept. */
    public void delete(String itemId) {
        http.request("DELETE", "/items/" + HttpCore.encode(Support.requireId(itemId, "itemId")), Map.of(), null, true);
    }

    /** Batch upsert (1-1000). A failing entry is reported in {@code errors}, it does not throw. */
    public BatchResult upsertMany(List<ItemImport> items) {
        if (items == null || items.isEmpty()) {
            throw new ValidationException("items must be a non-empty list");
        }
        ArrayNode array = HttpCore.mapper().createArrayNode();
        for (ItemImport i : items) {
            ObjectNode node = body(i.title(), i.description(), i.properties());
            ObjectNode entry = Support.object();
            entry.put("item_id", Support.requireId(i.itemId(), "itemId"));
            entry.setAll(node);
            array.add(entry);
        }
        ObjectNode body = Support.object();
        body.set("items", array);
        return Wire.batch(http.request("POST", "/items/import", Map.of(), body, true).data());
    }

    /** Alias of {@link #upsertMany}: the API's {@code POST /items/import} is a JSON batch upsert. ({@code import} is a Java keyword.) */
    public BatchResult importItems(List<ItemImport> items) {
        return upsertMany(items);
    }

    /** Batch delete (1-1000). Ids that don't exist are reported in {@code errors}. */
    public BatchResult deleteMany(List<String> itemIds) {
        if (itemIds == null || itemIds.isEmpty()) {
            throw new ValidationException("itemIds must be a non-empty list");
        }
        ArrayNode ids = HttpCore.mapper().createArrayNode();
        itemIds.forEach(id -> ids.add(Support.requireId(id, "itemId")));
        ObjectNode body = Support.object();
        body.set("item_ids", ids);
        return Wire.batch(http.request("POST", "/items/delete", Map.of(), body, true).data());
    }

    private static ObjectNode body(String title, String description, Map<String, Object> properties) {
        if (title == null || title.isEmpty()) {
            throw new ValidationException("an item needs a non-empty title");
        }
        ObjectNode node = Support.object();
        node.put("title", title);
        Support.put(node, "description", description);
        Support.putProperties(node, properties);
        return node;
    }
}
