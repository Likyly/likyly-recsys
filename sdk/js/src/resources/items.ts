import { compact, encodeSegment, requireId } from "../encoding.js";
import { ValidationError } from "../errors.js";
import type { HttpClient } from "../http.js";
import type { BatchResult, Item, ItemImport, ItemInput, ItemList, ListOptions, RequestOptions } from "../types.js";

/** `likyly.items` - your catalog. Needs the **secret** API key. */
export class ItemsResource {
  constructor(private readonly http: HttpClient) {}

  /** One item by your own id. */
  async get(itemId: string, options?: RequestOptions): Promise<Item> {
    const res = await this.http.request<WireItem>({ method: "GET", path: `/items/${encodeSegment(requireId(itemId, "itemId"))}`, idempotent: true, options });
    return toItem(res.data);
  }

  /** One page of the catalog. Pagination is `limit` + `offset` (the API's default is 100 per page); `total` is the whole catalog's size. */
  async list(listOptions: ListOptions = {}, options?: RequestOptions): Promise<ItemList> {
    const res = await this.http.request<WireItem[]>({
      method: "GET",
      path: "/items",
      query: { limit: listOptions.limit, offset: listOptions.offset },
      idempotent: true,
      options,
    });
    const total = res.headers.get("x-total-count");
    return { items: res.data.map(toItem), total: total === null ? undefined : Number(total), limit: listOptions.limit, offset: listOptions.offset ?? 0 };
  }

  /**
   * Creates the item, or replaces it if it exists - idempotent, safe to call as often as you like.
   * The body is the whole item: fields you leave out are cleared.
   */
  async upsert(itemId: string, item: ItemInput, options?: RequestOptions): Promise<Item> {
    const res = await this.http.request<WireItem>({
      method: "PUT",
      path: `/items/${encodeSegment(requireId(itemId, "itemId"))}`,
      body: itemBody(item),
      idempotent: true,
      options,
    });
    return toItem(res.data);
  }

  /** Removes the item from the catalog. Events already recorded for it are kept. */
  async delete(itemId: string, options?: RequestOptions): Promise<void> {
    await this.http.request({ method: "DELETE", path: `/items/${encodeSegment(requireId(itemId, "itemId"))}`, idempotent: true, options });
  }

  /** Batch upsert (1-1000 items) - each entry behaves like `upsert`. A failing entry is reported in `errors`. */
  async upsertMany(items: ItemImport[], options?: RequestOptions): Promise<BatchResult> {
    if (!Array.isArray(items) || items.length === 0) throw new ValidationError("items must be a non-empty array");
    const res = await this.http.request<WireBatch>({
      method: "POST",
      path: "/items/import",
      body: { items: items.map((i) => ({ item_id: requireId(i.itemId, "itemId"), ...itemBody(i) })) },
      idempotent: true, // an upsert: replaying it changes nothing
      options,
    });
    return toBatch(res.data);
  }

  /** Alias of {@link upsertMany}: the API's `POST /items/import` is a JSON batch upsert. (A CSV import exists only in the LIKYLY dashboard.) */
  import(items: ItemImport[], options?: RequestOptions): Promise<BatchResult> {
    return this.upsertMany(items, options);
  }

  /** Batch delete (1-1000 ids). Ids that don't exist are reported in `errors`. */
  async deleteMany(itemIds: string[], options?: RequestOptions): Promise<BatchResult> {
    if (!Array.isArray(itemIds) || itemIds.length === 0) throw new ValidationError("itemIds must be a non-empty array");
    const res = await this.http.request<WireBatch>({
      method: "POST",
      path: "/items/delete",
      body: { item_ids: itemIds.map((id) => requireId(id, "itemId")) },
      idempotent: true,
      options,
    });
    return toBatch(res.data);
  }
}

// ---- wire format (snake_case) <-> public types -----------------------------------------------

interface WireItem {
  item_id: string;
  title: string;
  description?: string | null;
  properties?: Record<string, unknown>;
}
export interface WireBatch {
  received: number;
  succeeded: number;
  failed: number;
  errors?: { index: number; id?: string | null; message: string }[];
}

function itemBody(item: ItemInput): Record<string, unknown> {
  if (item === null || typeof item !== "object" || typeof item.title !== "string" || item.title === "") {
    throw new ValidationError("an item needs a non-empty title");
  }
  return compact({ title: item.title, description: item.description, properties: item.properties });
}

export function toItem(w: WireItem): Item {
  return { itemId: w.item_id, title: w.title, description: w.description ?? undefined, properties: w.properties ?? {} };
}

export function toBatch(w: WireBatch): BatchResult {
  return {
    received: w.received,
    succeeded: w.succeeded,
    failed: w.failed,
    errors: (w.errors ?? []).map((e) => ({ index: e.index, id: e.id ?? undefined, message: e.message })),
  };
}
