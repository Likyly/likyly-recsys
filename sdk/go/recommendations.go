package likyly

import (
	"context"
	"strconv"
	"strings"
)

const defaultLimit = 10

// RecommendationsService is client.Recommendations. Use Get: send what you know and LIKYLY picks the best
// strategy. The other methods are the Advanced Recommendations - one strategy at a time.
type RecommendationsService struct{ c *core }

type wireExplanation struct {
	Reason             string   `json:"reason"`
	ContentSimilarity  *float64 `json:"content_similarity"`
	SemanticSimilarity *float64 `json:"semantic_similarity"`
	PopularityScore    *float64 `json:"popularity_score"`
	InteractionCount   *int     `json:"interaction_count"`
	InteractionLabel   *string  `json:"interaction_label"`
	CollaborativeScore *float64 `json:"collaborative_score"`
	SourceItemIDs      []string `json:"source_item_ids"`
	SimilarUsers       []struct {
		UserID        string   `json:"user_id"`
		SharedItemIDs []string `json:"shared_item_ids"`
	} `json:"similar_users"`
}

type wireRecommendedItem struct {
	ItemID      string           `json:"item_id"`
	Score       *float64         `json:"score"`
	Title       *string          `json:"title"`
	Description *string          `json:"description"`
	Properties  Properties       `json:"properties"`
	Explanation *wireExplanation `json:"explanation"`
}

type wireRecommendation struct {
	RecommendationID string                `json:"recommendation_id"`
	Strategy         string                `json:"strategy"`
	Placement        *string               `json:"placement"`
	Items            []wireRecommendedItem `json:"items"`
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func (w wireRecommendation) public() *RecommendationResponse {
	out := &RecommendationResponse{RecommendationID: w.RecommendationID, Strategy: w.Strategy, Placement: str(w.Placement), Items: make([]RecommendedItem, 0, len(w.Items))}
	for _, it := range w.Items {
		ri := RecommendedItem{ItemID: it.ItemID, Score: it.Score, Title: str(it.Title), Description: str(it.Description), Properties: it.Properties}
		if ri.Properties == nil {
			ri.Properties = Properties{}
		}
		if e := it.Explanation; e != nil {
			ex := &Explanation{
				Reason: e.Reason, ContentSimilarity: e.ContentSimilarity, SemanticSimilarity: e.SemanticSimilarity,
				PopularityScore: e.PopularityScore, InteractionCount: e.InteractionCount, InteractionLabel: str(e.InteractionLabel),
				CollaborativeScore: e.CollaborativeScore, SourceItemIDs: e.SourceItemIDs,
			}
			for _, u := range e.SimilarUsers {
				ex.SimilarUsers = append(ex.SimilarUsers, SimilarUser{UserID: u.UserID, SharedItemIDs: u.SharedItemIDs})
			}
			ri.Explanation = ex
		}
		out.Items = append(out.Items, ri)
	}
	return out
}

// Get returns recommendations for a user, an anonymous session, an item being viewed, viewed items - or
// nothing at all (then you get what is popular). You never choose the algorithm; Strategy in the response
// says what was used. Send RecommendationID back on the events that follow.
func (s *RecommendationsService) Get(ctx context.Context, req RecommendationRequest, opts ...CallOption) (*RecommendationResponse, error) {
	body := map[string]any{}
	for _, f := range []struct{ key, name, value string }{
		{"user_id", "UserID", req.UserID},
		{"session_id", "SessionID", req.SessionID},
		{"item_id", "ItemID", req.ItemID},
	} {
		if f.value == "" {
			continue
		}
		if _, err := requireID(f.value, f.name); err != nil {
			return nil, err
		}
		body[f.key] = f.value
	}
	if req.ViewedItemIDs != nil {
		for _, id := range req.ViewedItemIDs {
			if _, err := requireID(id, "ViewedItemIDs[]"); err != nil {
				return nil, err
			}
		}
		body["viewed_item_ids"] = req.ViewedItemIDs
	}
	if req.Placement != "" {
		body["placement"] = req.Placement
	}
	if req.Limit != 0 {
		body["count"] = req.Limit
	}
	if req.Debug {
		body["debug"] = true
	}
	// A read: repeating it only mints another recommendation ID, so it is retried.
	return s.send(ctx, call{method: "POST", path: "/getRec", body: body, idempotent: true, opts: opts})
}

// ---- Advanced Recommendations ---------------------------------------------------------------------
// One strategy at a time, for expert use. Ids travel in the URL path here, so they cannot contain "/".

// Popular returns the most popular items - the fallback for a visitor with no history at all.
func (s *RecommendationsService) Popular(ctx context.Context, o PopularOptions, opts ...CallOption) (*RecommendationResponse, error) {
	limit, err := limitOf(o.Limit)
	if err != nil {
		return nil, err
	}
	return s.advanced(ctx, "/getRec/popular/"+limit, o.AdvancedOptions, nil, opts)
}

// Similar returns items similar to one item (content similarity). No user needed.
func (s *RecommendationsService) Similar(ctx context.Context, o SimilarOptions, opts ...CallOption) (*RecommendationResponse, error) {
	item, err := requirePathSafeID(o.ItemID, "ItemID")
	if err != nil {
		return nil, err
	}
	limit, err := limitOf(o.Limit)
	if err != nil {
		return nil, err
	}
	return s.advanced(ctx, "/getRec/content/"+encodeSegment(item)+"/"+limit, o.AdvancedOptions, nil, opts)
}

// Collaborative: what users with similar histories liked. Needs a trained model.
func (s *RecommendationsService) Collaborative(ctx context.Context, o CollaborativeOptions, opts ...CallOption) (*RecommendationResponse, error) {
	user, err := requirePathSafeID(o.UserID, "UserID")
	if err != nil {
		return nil, err
	}
	limit, err := limitOf(o.Limit)
	if err != nil {
		return nil, err
	}
	return s.advanced(ctx, "/getRec/collaborative/"+encodeSegment(user)+"/"+limit, o.AdvancedOptions, nil, opts)
}

// Hybrid returns similar items, personalized for a user. Alpha (0-1) weighs the collaborative signal against
// content similarity.
func (s *RecommendationsService) Hybrid(ctx context.Context, o HybridOptions, opts ...CallOption) (*RecommendationResponse, error) {
	user, err := requirePathSafeID(o.UserID, "UserID")
	if err != nil {
		return nil, err
	}
	item, err := requirePathSafeID(o.ItemID, "ItemID")
	if err != nil {
		return nil, err
	}
	limit, err := limitOf(o.Limit)
	if err != nil {
		return nil, err
	}
	var extra []queryParam
	if o.Alpha != nil {
		extra = append(extra, queryParam{"alpha", strconv.FormatFloat(*o.Alpha, 'g', -1, 64)})
	}
	return s.advanced(ctx, "/getRec/hybrid/"+encodeSegment(user)+"/"+encodeSegment(item)+"/"+limit, o.AdvancedOptions, extra, opts)
}

// Session returns recency-weighted recommendations from what was viewed: pass ViewedItemIDs (an explicit
// list, oldest first) OR UserID (LIKYLY's own history of that user's views) - exactly one.
func (s *RecommendationsService) Session(ctx context.Context, o SessionOptions, opts ...CallOption) (*RecommendationResponse, error) {
	hasList, hasUser := o.ViewedItemIDs != nil, o.UserID != ""
	if hasList == hasUser {
		return nil, validationf("Session needs either ViewedItemIDs or UserID (exactly one)")
	}
	limit, err := limitOf(o.Limit)
	if err != nil {
		return nil, err
	}
	if hasUser {
		user, err := requirePathSafeID(o.UserID, "UserID")
		if err != nil {
			return nil, err
		}
		return s.advanced(ctx, "/getRec/sessionForUser/"+encodeSegment(user)+"/"+limit, o.AdvancedOptions, nil, opts)
	}
	if len(o.ViewedItemIDs) == 0 {
		return nil, validationf("ViewedItemIDs must contain at least one item id")
	}
	for _, id := range o.ViewedItemIDs {
		if _, err := requireID(id, "ViewedItemIDs[]"); err != nil {
			return nil, err
		}
		if strings.Contains(id, ",") {
			return nil, validationf(`an item id containing "," cannot be sent in this endpoint's comma-separated list - use Recommendations.Get`)
		}
	}
	extra := []queryParam{{"viewed_item_ids", strings.Join(o.ViewedItemIDs, ",")}, {"count", limit}}
	return s.advanced(ctx, "/getRec/session", o.AdvancedOptions, extra, opts)
}

func (s *RecommendationsService) advanced(ctx context.Context, path string, o AdvancedOptions, extra []queryParam, opts []CallOption) (*RecommendationResponse, error) {
	// response_format=object: always the same {recommendation_id, strategy, items} envelope
	query := []queryParam{{"response_format", "object"}}
	if o.Placement != "" {
		query = append(query, queryParam{"placement", o.Placement})
	}
	if o.SessionID != "" {
		query = append(query, queryParam{"session_id", o.SessionID})
	}
	query = append(query, extra...)
	return s.send(ctx, call{method: "GET", path: path, query: query, idempotent: true, opts: opts})
}

func (s *RecommendationsService) send(ctx context.Context, cl call) (*RecommendationResponse, error) {
	resp, err := s.c.request(ctx, cl)
	if err != nil {
		return nil, err
	}
	var w wireRecommendation
	if err := resp.decode(&w); err != nil {
		return nil, err
	}
	return w.public(), nil
}

func limitOf(limit int) (string, error) {
	if limit < 0 {
		return "", validationf("Limit must be a positive integer")
	}
	if limit == 0 {
		limit = defaultLimit
	}
	return strconv.Itoa(limit), nil
}
