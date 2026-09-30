package likyly

import "time"

// Properties is a free-form JSON object: price, currency, orderId, category, any field of your own.
// Keys are stored exactly as you send them and never renamed by the SDK.
type Properties = map[string]any

// ---- Items ---------------------------------------------------------------------------------------

// Item is a catalog entry.
type Item struct {
	// ItemID is your own identifier - any string (SKU-123, a UUID, gid://shopify/Product/123).
	ItemID      string     `json:"itemId"`
	Title       string     `json:"title"`
	Description string     `json:"description,omitempty"`
	Properties  Properties `json:"properties"`
}

// ItemInput is what you send to create or replace an item. Only Title is required.
type ItemInput struct {
	Title       string `json:"title"`
	Description string `json:"description,omitempty"`
	// Properties: category and description feed content similarity; everything else is stored as is.
	Properties Properties `json:"properties,omitempty"`
}

// ItemImport is an item with its id, for Items.UpsertMany.
type ItemImport struct {
	ItemID      string     `json:"itemId"`
	Title       string     `json:"title"`
	Description string     `json:"description,omitempty"`
	Properties  Properties `json:"properties,omitempty"`
}

// ListOptions paginates a list: zero values mean "the API's default".
type ListOptions struct {
	// Limit is the page size (1-1000; the API's default for items is 100, users are all returned).
	Limit int `json:"limit,omitempty"`
	// Offset is the number of rows to skip.
	Offset int `json:"offset,omitempty"`
}

// ItemList is one page of the catalog.
type ItemList struct {
	Items []Item `json:"items"`
	// Total is the catalog's whole size (X-Total-Count); nil if the API did not report it.
	Total *int `json:"total"`
	// Limit is the page size you asked for (0 = the API's default).
	Limit  int `json:"limit"`
	Offset int `json:"offset"`
}

// ---- Users ---------------------------------------------------------------------------------------

// User is an optional user profile.
type User struct {
	UserID     string     `json:"userId"`
	Properties Properties `json:"properties"`
}

// UserInput is what you send to create or replace a user.
type UserInput struct {
	// Properties: country, segment, language, ... - whatever describes your users.
	Properties Properties `json:"properties,omitempty"`
}

// UserImport is a user with its id, for Users.Import.
type UserImport struct {
	UserID     string     `json:"userId"`
	Properties Properties `json:"properties,omitempty"`
}

// UserList is one page of users.
type UserList struct {
	Users  []User `json:"users"`
	Total  *int   `json:"total"`
	Limit  int    `json:"limit"`
	Offset int    `json:"offset"`
}

// BatchError describes one failed entry of a batch call.
type BatchError struct {
	Index   int    `json:"index"`
	ID      string `json:"id,omitempty"`
	Message string `json:"message"`
}

// BatchResult is the result of a batch call. A failing entry is reported in Errors, it does not return an error.
type BatchResult struct {
	Received  int          `json:"received"`
	Succeeded int          `json:"succeeded"`
	Failed    int          `json:"failed"`
	Errors    []BatchError `json:"errors"`
}

// ---- Events --------------------------------------------------------------------------------------

// EventInput is an interaction. ItemID and at least one of UserID / SessionID are required; sending both
// ties an anonymous session to the user.
type EventInput struct {
	ItemID string `json:"itemId"`
	UserID string `json:"userId,omitempty"`
	// SessionID is an anonymous visitor / browsing session - no login needed.
	SessionID string `json:"sessionId,omitempty"`
	// RecommendationID is the ID of the recommendation that surfaced this item - enables attribution.
	RecommendationID string `json:"recommendationId,omitempty"`
	// Placement is a free-form label of where this happened: homepage, product_page, cart, ...
	Placement string `json:"placement,omitempty"`
	// Quantity: 0 means "not set".
	Quantity int `json:"quantity,omitempty"`
	// OccurredAt: zero means "now" (the server's time).
	OccurredAt time.Time  `json:"occurredAt,omitempty"`
	Properties Properties `json:"properties,omitempty"`
	// EventID is an idempotency key, unique per account: replaying an event with the same EventID records
	// nothing and returns Duplicate = true. Set it on purchases - it makes retries safe.
	EventID string `json:"eventId,omitempty"`
}

// TypedEvent is an event of any type, for Events.TrackMany. Type is an open string ("view", "favorite", ...).
type TypedEvent struct {
	Type string `json:"type"`
	EventInput
}

// EventResult is the answer to a recorded event.
type EventResult struct {
	Message string `json:"message"`
	EventID string `json:"eventId,omitempty"`
	// Duplicate is true if this EventID was already recorded - nothing was written.
	Duplicate bool `json:"duplicate"`
}

// EventBatchResult is the answer to Events.TrackMany.
type EventBatchResult struct {
	Received int `json:"received"`
	Accepted int `json:"accepted"`
	// Duplicates are events skipped because their EventID was already recorded.
	Duplicates int `json:"duplicates"`
}

// ---- Recommendations -----------------------------------------------------------------------------

// RecommendationRequest: send whatever you know and LIKYLY chooses the best strategy. Every field is optional.
type RecommendationRequest struct {
	UserID    string `json:"userId,omitempty"`
	SessionID string `json:"sessionId,omitempty"`
	// ItemID is the item being looked at (e.g. the product page).
	ItemID string `json:"itemId,omitempty"`
	// ViewedItemIDs are the recently viewed items, oldest first.
	ViewedItemIDs []string `json:"viewedItemIds,omitempty"`
	// Placement is a free-form label of where the recommendations will be shown.
	Placement string `json:"placement,omitempty"`
	// Limit is how many items to return (1-100, default 10).
	Limit int `json:"limit,omitempty"`
	// Debug adds diagnostic detail in Explanation. Secret key only.
	Debug bool `json:"debug,omitempty"`
}

// SimilarUser is a user whose history contributed to a recommendation (Debug, secret key only).
type SimilarUser struct {
	UserID        string   `json:"userId"`
	SharedItemIDs []string `json:"sharedItemIds"`
}

// Explanation says why an item was recommended.
type Explanation struct {
	Reason             string   `json:"reason"`
	ContentSimilarity  *float64 `json:"contentSimilarity,omitempty"`
	SemanticSimilarity *float64 `json:"semanticSimilarity,omitempty"`
	PopularityScore    *float64 `json:"popularityScore,omitempty"`
	InteractionCount   *int     `json:"interactionCount,omitempty"`
	InteractionLabel   string   `json:"interactionLabel,omitempty"`
	CollaborativeScore *float64 `json:"collaborativeScore,omitempty"`
	SourceItemIDs      []string `json:"sourceItemIds,omitempty"`
	// SimilarUsers is only present with Debug and a secret key.
	SimilarUsers []SimilarUser `json:"similarUsers,omitempty"`
}

// RecommendedItem is one recommended item.
type RecommendedItem struct {
	ItemID      string       `json:"itemId"`
	Score       *float64     `json:"score,omitempty"`
	Title       string       `json:"title,omitempty"`
	Description string       `json:"description,omitempty"`
	Properties  Properties   `json:"properties"`
	Explanation *Explanation `json:"explanation,omitempty"`
}

// RecommendationResponse is what every recommendation call returns.
type RecommendationResponse struct {
	// RecommendationID: send it back on the impression / click / add_to_cart / purchase events for these items.
	RecommendationID string `json:"recommendationId"`
	// Strategy is what LIKYLY used: hybrid, content, collaborative, session or popular.
	Strategy  string            `json:"strategy"`
	Placement string            `json:"placement,omitempty"`
	Items     []RecommendedItem `json:"items"`
}

// AdvancedOptions are shared by the Advanced Recommendations (one strategy at a time).
type AdvancedOptions struct {
	Placement string `json:"placement,omitempty"`
	// SessionID attaches the recommendation to an anonymous session, for attribution.
	SessionID string `json:"sessionId,omitempty"`
	// Limit is how many items to return (default 10).
	Limit int `json:"limit,omitempty"`
}

// PopularOptions: the most popular items.
type PopularOptions struct{ AdvancedOptions }

// SimilarOptions: items similar to ItemID.
type SimilarOptions struct {
	AdvancedOptions
	ItemID string `json:"itemId"`
}

// CollaborativeOptions: what users with similar histories liked.
type CollaborativeOptions struct {
	AdvancedOptions
	UserID string `json:"userId"`
}

// HybridOptions: similar items, personalized for a user.
type HybridOptions struct {
	AdvancedOptions
	UserID string `json:"userId"`
	ItemID string `json:"itemId"`
	// Alpha weighs the collaborative signal against content similarity: 0 = pure content, 1 = pure
	// collaborative. nil means the API's default (0.5).
	Alpha *float64 `json:"alpha,omitempty"`
}

// SessionOptions: give ViewedItemIDs (an explicit list, oldest first) OR UserID (LIKYLY's own history of that
// user's views) - exactly one.
type SessionOptions struct {
	AdvancedOptions
	ViewedItemIDs []string `json:"viewedItemIds,omitempty"`
	UserID        string   `json:"userId,omitempty"`
}
