package likyly

import (
	"context"
	"time"
)

// EventsService is what your visitors do: client.Events. Works with the public key from a browser or the
// secret key from your backend. Track is the one mechanism; View, Click, ... are shortcuts that call it with
// the matching event type.
type EventsService struct{ c *core }

type wireEventResult struct {
	Message   string  `json:"message"`
	EventID   *string `json:"event_id"`
	Duplicate bool    `json:"duplicate"`
}

// Track records one event of any type: "view", "click", ... or your own ("favorite", "share", ...). Event
// types are open strings - the six helpers are conveniences, not a closed list.
//
// Retries: a failed call is only retried automatically when it carries an EventID (then a replay is
// harmless); without one, an ambiguous failure is returned rather than risking a duplicate.
func (s *EventsService) Track(ctx context.Context, eventType string, event EventInput, opts ...CallOption) (*EventResult, error) {
	t, err := requireEventType(eventType)
	if err != nil {
		return nil, err
	}
	body, err := eventBody(event)
	if err != nil {
		return nil, err
	}
	resp, err := s.c.request(ctx, call{method: "POST", path: "/events/" + encodeSegment(t), body: body, idempotent: event.EventID != "", opts: opts})
	if err != nil {
		return nil, err
	}
	var w wireEventResult
	if err := resp.decode(&w); err != nil {
		return nil, err
	}
	out := &EventResult{Message: w.Message, Duplicate: w.Duplicate}
	if w.EventID != nil {
		out.EventID = *w.EventID
	}
	return out, nil
}

// TrackMany records up to 1000 events in one call, each with its own Type. Validation is all-or-nothing.
func (s *EventsService) TrackMany(ctx context.Context, events []TypedEvent, opts ...CallOption) (*EventBatchResult, error) {
	if len(events) == 0 {
		return nil, validationf("events must be a non-empty slice")
	}
	entries := make([]any, 0, len(events))
	allKeyed := true
	for _, e := range events {
		t, err := requireEventType(e.Type)
		if err != nil {
			return nil, err
		}
		body, err := eventBody(e.EventInput)
		if err != nil {
			return nil, err
		}
		body["event_type"] = t
		entries = append(entries, body)
		if e.EventID == "" {
			allKeyed = false
		}
	}
	resp, err := s.c.request(ctx, call{method: "POST", path: "/events/batch", body: map[string]any{"events": entries}, idempotent: allKeyed, opts: opts})
	if err != nil {
		return nil, err
	}
	var out EventBatchResult
	if err := resp.decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Impression: the item was shown to the visitor (send the RecommendationID it came with).
func (s *EventsService) Impression(ctx context.Context, e EventInput, opts ...CallOption) (*EventResult, error) {
	return s.Track(ctx, "impression", e, opts...)
}

// View: the visitor looked at the item (a product page, an article).
func (s *EventsService) View(ctx context.Context, e EventInput, opts ...CallOption) (*EventResult, error) {
	return s.Track(ctx, "view", e, opts...)
}

// Click: the visitor clicked the item.
func (s *EventsService) Click(ctx context.Context, e EventInput, opts ...CallOption) (*EventResult, error) {
	return s.Track(ctx, "click", e, opts...)
}

// AddToCart: the visitor added the item to their cart.
func (s *EventsService) AddToCart(ctx context.Context, e EventInput, opts ...CallOption) (*EventResult, error) {
	return s.Track(ctx, "add_to_cart", e, opts...)
}

// RemoveFromCart: the visitor removed the item from their cart.
func (s *EventsService) RemoveFromCart(ctx context.Context, e EventInput, opts ...CallOption) (*EventResult, error) {
	return s.Track(ctx, "remove_from_cart", e, opts...)
}

// Purchase: the visitor bought the item. Set EventID (e.g. "purchase_<orderId>_<itemId>") so a retry can
// never count the purchase twice.
func (s *EventsService) Purchase(ctx context.Context, e EventInput, opts ...CallOption) (*EventResult, error) {
	return s.Track(ctx, "purchase", e, opts...)
}

// eventBody: Properties is passed through untouched - its keys are yours.
func eventBody(e EventInput) (map[string]any, error) {
	itemID, err := requireID(e.ItemID, "ItemID")
	if err != nil {
		return nil, err
	}
	if e.UserID == "" && e.SessionID == "" {
		return nil, validationf("an event needs a UserID or a SessionID (or both)")
	}
	body := map[string]any{"item_id": itemID}
	for _, f := range []struct{ key, name, value string }{
		{"user_id", "UserID", e.UserID},
		{"session_id", "SessionID", e.SessionID},
	} {
		if f.value == "" {
			continue
		}
		if _, err := requireID(f.value, f.name); err != nil {
			return nil, err
		}
		body[f.key] = f.value
	}
	setIf := func(key, value string) {
		if value != "" {
			body[key] = value
		}
	}
	setIf("event_id", e.EventID)
	setIf("recommendation_id", e.RecommendationID)
	setIf("placement", e.Placement)
	if e.Quantity != 0 {
		body["quantity"] = e.Quantity
	}
	if !e.OccurredAt.IsZero() {
		body["occurred_at"] = e.OccurredAt.UTC().Format(time.RFC3339Nano)
	}
	if e.Properties != nil {
		body["properties"] = e.Properties
	}
	return body, nil
}
