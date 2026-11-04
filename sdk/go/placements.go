package likyly

import "context"

// PlacementsService is client.Placements - the recommended way to ask for recommendations. A placement
// ("what to recommend, where, with what fallback and filters") is configured once in LIKYLY (dashboard or MCP
// admin tools, never from this SDK) and called by its slug from here on. client.Recommendations remains for
// advanced, per-call use with no saved configuration.
type PlacementsService struct{ c *core }

type wirePlacementRecommendation struct {
	RecommendationID string                `json:"recommendation_id"`
	Placement        string                `json:"placement"`
	StrategyUsed     string                `json:"strategy_used"`
	Items            []wireRecommendedItem `json:"items"`
}

// Recommend returns the recommendations of one placement. Send whatever context you have (ItemID, UserID,
// SessionID, ...): the placement's own configuration decides the strategy, fallback and filters. Send
// RecommendationID back on the events that follow to attribute them.
func (s *PlacementsService) Recommend(ctx context.Context, req PlacementRequest, opts ...CallOption) (*PlacementResponse, error) {
	placement, err := requireID(req.Placement, "Placement")
	if err != nil {
		return nil, err
	}
	if req.Limit < 0 {
		return nil, validationf("Limit must be a positive integer")
	}
	body := map[string]any{}
	if req.Context != nil {
		body["context"] = placementContextBody(req.Context)
	}
	if req.Limit != 0 {
		body["limit"] = req.Limit
	}
	// A read in effect: each call mints a fresh recommendation ID, so repeating it after a dropped
	// connection is harmless - it is retried.
	resp, err := s.c.request(ctx, call{method: "POST", path: "/placements/" + encodeSegment(placement) + "/recommend", body: body, idempotent: true, opts: opts})
	if err != nil {
		return nil, err
	}
	var w wirePlacementRecommendation
	if err := resp.decode(&w); err != nil {
		return nil, err
	}
	return &PlacementResponse{RecommendationID: w.RecommendationID, Placement: w.Placement, StrategyUsed: w.StrategyUsed, Items: publicItems(w.Items)}, nil
}

// placementContextBody leaves out every field that is not set. CustomContext is passed through untouched.
func placementContextBody(pc *PlacementContext) map[string]any {
	body := map[string]any{}
	for _, f := range []struct{ key, value string }{
		{"current_item_id", pc.ItemID},
		{"category_id", pc.CategoryID},
		{"collection_id", pc.CollectionID},
		{"user_id", pc.UserID},
		{"anonymous_id", pc.AnonymousID},
		{"session_id", pc.SessionID},
		{"locale", pc.Locale},
	} {
		if f.value != "" {
			body[f.key] = f.value
		}
	}
	if pc.CartItemIDs != nil {
		body["cart_item_ids"] = pc.CartItemIDs
	}
	if pc.CustomContext != nil {
		body["custom_context"] = pc.CustomContext
	}
	return body
}
