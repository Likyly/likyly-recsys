package likyly

import "context"

// UsersService is your optional user profiles: client.Users. Needs the secret API key (profiles are personal
// data). You don't have to create a user before sending events for them.
type UsersService struct{ c *core }

type wireUser struct {
	UserID     string     `json:"user_id"`
	Properties Properties `json:"properties"`
}

func (w wireUser) public() User {
	u := User{UserID: w.UserID, Properties: w.Properties}
	if u.Properties == nil {
		u.Properties = Properties{}
	}
	return u
}

// Get returns one user.
func (s *UsersService) Get(ctx context.Context, userID string, opts ...CallOption) (*User, error) {
	id, err := requireID(userID, "userID")
	if err != nil {
		return nil, err
	}
	resp, err := s.c.request(ctx, call{method: "GET", path: "/users/" + encodeSegment(id), idempotent: true, opts: opts})
	if err != nil {
		return nil, err
	}
	var w wireUser
	if err := resp.decode(&w); err != nil {
		return nil, err
	}
	u := w.public()
	return &u, nil
}

// List returns one page of users. Pagination is Limit + Offset; without a Limit the API returns every user.
func (s *UsersService) List(ctx context.Context, page ListOptions, opts ...CallOption) (*UserList, error) {
	resp, err := s.c.request(ctx, call{method: "GET", path: "/users", query: pageQuery(page), idempotent: true, opts: opts})
	if err != nil {
		return nil, err
	}
	var ws []wireUser
	if err := resp.decode(&ws); err != nil {
		return nil, err
	}
	out := &UserList{Users: make([]User, 0, len(ws)), Total: totalOf(resp), Limit: page.Limit, Offset: page.Offset}
	for _, w := range ws {
		out.Users = append(out.Users, w.public())
	}
	return out, nil
}

// Upsert creates or replaces the profile (idempotent). Properties is free-form: country, segment, language, ...
func (s *UsersService) Upsert(ctx context.Context, userID string, user UserInput, opts ...CallOption) (*User, error) {
	id, err := requireID(userID, "userID")
	if err != nil {
		return nil, err
	}
	body := map[string]any{}
	if user.Properties != nil {
		body["properties"] = user.Properties
	}
	resp, err := s.c.request(ctx, call{method: "PUT", path: "/users/" + encodeSegment(id), body: body, idempotent: true, opts: opts})
	if err != nil {
		return nil, err
	}
	var w wireUser
	if err := resp.decode(&w); err != nil {
		return nil, err
	}
	u := w.public()
	return &u, nil
}

// Delete erases the user: the profile AND every event recorded for them.
func (s *UsersService) Delete(ctx context.Context, userID string, opts ...CallOption) error {
	id, err := requireID(userID, "userID")
	if err != nil {
		return err
	}
	_, err = s.c.request(ctx, call{method: "DELETE", path: "/users/" + encodeSegment(id), idempotent: true, opts: opts})
	return err
}

// Import is a batch upsert (1-1000 users). A failing entry is reported in BatchResult.Errors.
func (s *UsersService) Import(ctx context.Context, users []UserImport, opts ...CallOption) (*BatchResult, error) {
	if len(users) == 0 {
		return nil, validationf("users must be a non-empty slice")
	}
	entries := make([]any, 0, len(users))
	for _, u := range users {
		id, err := requireID(u.UserID, "userID")
		if err != nil {
			return nil, err
		}
		entry := map[string]any{"user_id": id}
		if u.Properties != nil {
			entry["properties"] = u.Properties
		}
		entries = append(entries, entry)
	}
	resp, err := s.c.request(ctx, call{method: "POST", path: "/users/import", body: map[string]any{"users": entries}, idempotent: true, opts: opts})
	if err != nil {
		return nil, err
	}
	var w wireBatch
	if err := resp.decode(&w); err != nil {
		return nil, err
	}
	return w.public(), nil
}
