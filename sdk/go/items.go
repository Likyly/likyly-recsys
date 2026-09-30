package likyly

import (
	"context"
	"strconv"
)

// ItemsService is your catalog: client.Items. Needs the secret API key.
type ItemsService struct{ c *core }

type wireItem struct {
	ItemID      string     `json:"item_id"`
	Title       string     `json:"title"`
	Description *string    `json:"description"`
	Properties  Properties `json:"properties"`
}

func (w wireItem) public() Item {
	it := Item{ItemID: w.ItemID, Title: w.Title, Properties: w.Properties}
	if w.Description != nil {
		it.Description = *w.Description
	}
	if it.Properties == nil {
		it.Properties = Properties{}
	}
	return it
}

type wireBatch struct {
	Received  int `json:"received"`
	Succeeded int `json:"succeeded"`
	Failed    int `json:"failed"`
	Errors    []struct {
		Index   int     `json:"index"`
		ID      *string `json:"id"`
		Message string  `json:"message"`
	} `json:"errors"`
}

func (w wireBatch) public() *BatchResult {
	out := &BatchResult{Received: w.Received, Succeeded: w.Succeeded, Failed: w.Failed, Errors: []BatchError{}}
	for _, e := range w.Errors {
		be := BatchError{Index: e.Index, Message: e.Message}
		if e.ID != nil {
			be.ID = *e.ID
		}
		out.Errors = append(out.Errors, be)
	}
	return out
}

// Get returns one item by your own id.
func (s *ItemsService) Get(ctx context.Context, itemID string, opts ...CallOption) (*Item, error) {
	id, err := requireID(itemID, "itemID")
	if err != nil {
		return nil, err
	}
	var w wireItem
	if err := s.do(ctx, call{method: "GET", path: "/items/" + encodeSegment(id), idempotent: true, opts: opts}, &w); err != nil {
		return nil, err
	}
	it := w.public()
	return &it, nil
}

// List returns one page of the catalog. Pagination is Limit + Offset (the API's default is 100 per page);
// Total is the whole catalog's size.
func (s *ItemsService) List(ctx context.Context, page ListOptions, opts ...CallOption) (*ItemList, error) {
	var ws []wireItem
	resp, err := s.doResp(ctx, call{method: "GET", path: "/items", query: pageQuery(page), idempotent: true, opts: opts}, &ws)
	if err != nil {
		return nil, err
	}
	out := &ItemList{Items: make([]Item, 0, len(ws)), Total: totalOf(resp), Limit: page.Limit, Offset: page.Offset}
	for _, w := range ws {
		out.Items = append(out.Items, w.public())
	}
	return out, nil
}

// Upsert creates the item, or replaces it if it exists - idempotent, safe to call as often as you like.
// The body is the whole item: fields you leave out are cleared.
func (s *ItemsService) Upsert(ctx context.Context, itemID string, item ItemInput, opts ...CallOption) (*Item, error) {
	id, err := requireID(itemID, "itemID")
	if err != nil {
		return nil, err
	}
	body, err := itemBody(item.Title, item.Description, item.Properties)
	if err != nil {
		return nil, err
	}
	var w wireItem
	if err := s.do(ctx, call{method: "PUT", path: "/items/" + encodeSegment(id), body: body, idempotent: true, opts: opts}, &w); err != nil {
		return nil, err
	}
	it := w.public()
	return &it, nil
}

// Delete removes the item from the catalog. Events already recorded for it are kept.
func (s *ItemsService) Delete(ctx context.Context, itemID string, opts ...CallOption) error {
	id, err := requireID(itemID, "itemID")
	if err != nil {
		return err
	}
	_, err = s.c.request(ctx, call{method: "DELETE", path: "/items/" + encodeSegment(id), idempotent: true, opts: opts})
	return err
}

// UpsertMany is a batch upsert (1-1000 items) - each entry behaves like Upsert. A failing entry is reported
// in BatchResult.Errors.
func (s *ItemsService) UpsertMany(ctx context.Context, items []ItemImport, opts ...CallOption) (*BatchResult, error) {
	if len(items) == 0 {
		return nil, validationf("items must be a non-empty slice")
	}
	entries := make([]any, 0, len(items))
	for _, it := range items {
		id, err := requireID(it.ItemID, "itemID")
		if err != nil {
			return nil, err
		}
		body, err := itemBody(it.Title, it.Description, it.Properties)
		if err != nil {
			return nil, err
		}
		body["item_id"] = id
		entries = append(entries, body)
	}
	var w wireBatch
	// An upsert: replaying it changes nothing, so it is retried.
	if err := s.do(ctx, call{method: "POST", path: "/items/import", body: map[string]any{"items": entries}, idempotent: true, opts: opts}, &w); err != nil {
		return nil, err
	}
	return w.public(), nil
}

// Import is an alias of UpsertMany: the API's POST /items/import is a JSON batch upsert. (A CSV import exists
// only in the LIKYLY dashboard.)
func (s *ItemsService) Import(ctx context.Context, items []ItemImport, opts ...CallOption) (*BatchResult, error) {
	return s.UpsertMany(ctx, items, opts...)
}

// DeleteMany is a batch delete (1-1000 ids). Ids that don't exist are reported in BatchResult.Errors.
func (s *ItemsService) DeleteMany(ctx context.Context, itemIDs []string, opts ...CallOption) (*BatchResult, error) {
	if len(itemIDs) == 0 {
		return nil, validationf("itemIDs must be a non-empty slice")
	}
	for _, id := range itemIDs {
		if _, err := requireID(id, "itemID"); err != nil {
			return nil, err
		}
	}
	var w wireBatch
	if err := s.do(ctx, call{method: "POST", path: "/items/delete", body: map[string]any{"item_ids": itemIDs}, idempotent: true, opts: opts}, &w); err != nil {
		return nil, err
	}
	return w.public(), nil
}

func (s *ItemsService) do(ctx context.Context, cl call, out any) error {
	_, err := s.doResp(ctx, cl, out)
	return err
}

func (s *ItemsService) doResp(ctx context.Context, cl call, out any) (*response, error) {
	resp, err := s.c.request(ctx, cl)
	if err != nil {
		return nil, err
	}
	return resp, resp.decode(out)
}

func itemBody(title, description string, properties Properties) (map[string]any, error) {
	if title == "" {
		return nil, validationf("an item needs a non-empty Title")
	}
	body := map[string]any{"title": title}
	if description != "" {
		body["description"] = description
	}
	if properties != nil {
		body["properties"] = properties
	}
	return body, nil
}

func pageQuery(p ListOptions) []queryParam {
	var q []queryParam
	if p.Limit != 0 {
		q = append(q, queryParam{"limit", strconv.Itoa(p.Limit)})
	}
	if p.Offset != 0 {
		q = append(q, queryParam{"offset", strconv.Itoa(p.Offset)})
	}
	return q
}

func totalOf(r *response) *int {
	v, ok := r.headers["x-total-count"]
	if !ok {
		return nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return nil
	}
	return &n
}
