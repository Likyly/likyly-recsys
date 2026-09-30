package likyly

import (
	"errors"
	"fmt"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"
)

// End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless
// LIKYLY_TEST_URL, LIKYLY_TEST_SECRET_KEY and LIKYLY_TEST_PUBLIC_KEY are set.

const gid = "gid://shopify/Product/123456"

func liveClients(t *testing.T) (server, browser func() *Client, baseURL string) {
	t.Helper()
	url, secret, public := os.Getenv("LIKYLY_TEST_URL"), os.Getenv("LIKYLY_TEST_SECRET_KEY"), os.Getenv("LIKYLY_TEST_PUBLIC_KEY")
	if url == "" || secret == "" || public == "" {
		t.Skip("LIKYLY_TEST_* not set")
	}
	catalog := fmt.Sprintf("go-%d", time.Now().UnixNano())
	mk := func(key string) func() *Client {
		return func() *Client {
			c, err := New(key, WithBaseURL(url), WithCatalog(catalog))
			if err != nil {
				t.Fatal(err)
			}
			return c
		}
	}
	return mk(secret), mk(public), url
}

// must unwraps a (value, error) result; a failure panics, which `go test` reports as a failed test with the error.
func must[T any](v T, err error) T {
	if err != nil {
		panic(err)
	}
	return v
}

func adv(limit int) AdvancedOptions { return AdvancedOptions{Limit: limit} }

func TestLive(t *testing.T) {
	server, browser, baseURL := liveClients(t)
	bg := ctx

	t.Run("items: upsert / get / list / delete, with ids of any shape", func(t *testing.T) {
		c := server()
		created := must(c.Items.Upsert(bg, "SKU-123", ItemInput{Title: "Nike Air Max", Description: "Running shoe", Properties: Properties{"category": "shoes", "brand": "Nike", "price": 129.9}}))
		if created.ItemID != "SKU-123" || created.Properties["brand"] != "Nike" || created.Properties["price"] != 129.9 {
			t.Errorf("%+v", created)
		}
		must(c.Items.Upsert(bg, gid, ItemInput{Title: "Shopify boot", Description: "warm winter boot", Properties: Properties{"category": "boots"}}))
		must(c.Items.Upsert(bg, "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", ItemInput{Title: "UUID shoe", Description: "trail running shoe", Properties: Properties{"category": "shoes"}}))
		if got := must(c.Items.Get(bg, gid)); got.Title != "Shopify boot" {
			t.Errorf("%+v", got)
		}

		page := must(c.Items.List(bg, ListOptions{Limit: 2}))
		if len(page.Items) != 2 || page.Total == nil || *page.Total != 3 {
			t.Errorf("%+v", page)
		}

		many := must(c.Items.UpsertMany(bg, []ItemImport{
			{ItemID: "SKU-A", Title: "Adidas", Description: "road running shoe", Properties: Properties{"category": "shoes"}},
			{ItemID: "SKU-B", Title: "Asics", Description: "road running shoe", Properties: Properties{"category": "shoes"}},
		}))
		if many.Succeeded != 2 {
			t.Errorf("%+v", many)
		}

		if err := c.Items.Delete(bg, "SKU-B"); err != nil {
			t.Fatal(err)
		}
		_, err := c.Items.Get(bg, "SKU-B")
		wantErr[*NotFoundError](t, err)
		gone := must(c.Items.DeleteMany(bg, []string{"SKU-A", "ghost"}))
		if gone.Succeeded != 1 || gone.Failed != 1 || gone.Errors[0].ID != "ghost" {
			t.Errorf("%+v", gone)
		}
		must(c.Items.Upsert(bg, "SKU-A", ItemInput{Title: "Adidas", Description: "road running shoe", Properties: Properties{"category": "shoes"}}))
	})

	t.Run("users: upsert / get / list / delete with free-form properties", func(t *testing.T) {
		c := server()
		u := must(c.Users.Upsert(bg, "user_123", UserInput{Properties: Properties{"country": "FR", "segment": "premium", "language": "fr"}}))
		if u.UserID != "user_123" {
			t.Errorf("%+v", u)
		}
		if got := must(c.Users.Get(bg, "user_123")); got.Properties["segment"] != "premium" {
			t.Errorf("%+v", got)
		}
		found := false
		for _, x := range must(c.Users.List(bg, ListOptions{Limit: 10})).Users {
			found = found || x.UserID == "user_123"
		}
		if !found {
			t.Error("user_123 not listed")
		}
		if imp := must(c.Users.Import(bg, []UserImport{{UserID: "u_imp", Properties: Properties{"country": "DE"}}})); imp.Succeeded != 1 {
			t.Errorf("%+v", imp)
		}
		if err := c.Users.Delete(bg, "u_imp"); err != nil {
			t.Fatal(err)
		}
		_, err := c.Users.Get(bg, "u_imp")
		wantErr[*NotFoundError](t, err)
	})

	t.Run("events: identified, anonymous, both, custom, idempotent purchase, batch - with the PUBLIC key", func(t *testing.T) {
		c := browser()
		must(c.Events.View(bg, EventInput{UserID: "user_123", ItemID: "SKU-123"}))
		must(c.Events.View(bg, EventInput{SessionID: "sess_123", ItemID: "SKU-123"}))
		must(c.Events.View(bg, EventInput{UserID: "user_789", SessionID: "sess_123", ItemID: gid}))
		must(c.Events.Track(bg, "favorite", EventInput{UserID: "user_123", ItemID: "SKU-123", Properties: Properties{"source": "wishlist"}}))
		must(c.Events.AddToCart(bg, EventInput{UserID: "user_123", ItemID: "SKU-123", Quantity: 1}))
		must(c.Events.RemoveFromCart(bg, EventInput{UserID: "user_123", ItemID: "SKU-123", Quantity: 1}))
		purchase := EventInput{EventID: fmt.Sprintf("purchase_%d", time.Now().UnixNano()), UserID: "user_123", ItemID: "SKU-123", Quantity: 1,
			Properties: Properties{"price": 129.9, "currency": "EUR", "orderId": "ORDER-9281"}}
		if must(c.Events.Purchase(bg, purchase)).Duplicate {
			t.Error("first purchase reported as duplicate")
		}
		if !must(c.Events.Purchase(bg, purchase)).Duplicate {
			t.Error("replayed purchase not reported as duplicate")
		}
		batch := must(c.Events.TrackMany(bg, []TypedEvent{
			{Type: "view", EventInput: EventInput{UserID: "user_123", ItemID: "SKU-A"}},
			{Type: "click", EventInput: EventInput{SessionID: "sess_123", ItemID: "SKU-A"}},
		}))
		if *batch != (EventBatchResult{Received: 2, Accepted: 2, Duplicates: 0}) {
			t.Errorf("%+v", batch)
		}
	})

	t.Run("recommendations: get in every context, then attribution through the recommendation id", func(t *testing.T) {
		c := browser()
		forUser := must(c.Recommendations.Get(bg, RecommendationRequest{UserID: "user_123", Placement: "homepage", Limit: 3}))
		if !regexp.MustCompile(`^rec_[0-9A-Z]{26}$`).MatchString(forUser.RecommendationID) || forUser.Placement != "homepage" || len(forUser.Items) == 0 || len(forUser.Items) > 3 {
			t.Fatalf("%+v", forUser)
		}
		if got := must(c.Recommendations.Get(bg, RecommendationRequest{ItemID: "SKU-123", Limit: 2})).Strategy; got != "content" {
			t.Errorf("item -> %s", got)
		}
		if got := must(c.Recommendations.Get(bg, RecommendationRequest{SessionID: "sess_123", ViewedItemIDs: []string{"SKU-123"}, Limit: 2})).Strategy; got != "session" {
			t.Errorf("session -> %s", got)
		}
		if got := must(c.Recommendations.Get(bg, RecommendationRequest{ItemID: gid, Limit: 2})).Strategy; got != "content" {
			t.Errorf("id with slashes -> %s", got) // an id with "/" travels in the body
		}
		if must(c.Recommendations.Get(bg, RecommendationRequest{Limit: 2})).RecommendationID == "" {
			t.Error("empty request gave no recommendation id")
		}

		shown := forUser.Items[0]
		must(c.Events.Impression(bg, EventInput{UserID: "user_123", ItemID: shown.ItemID, RecommendationID: forUser.RecommendationID, Placement: "homepage"}))
		if click := must(c.Events.Click(bg, EventInput{UserID: "user_123", ItemID: shown.ItemID, RecommendationID: forUser.RecommendationID, Placement: "homepage"})); click.Duplicate {
			t.Error("click reported as duplicate")
		}
	})

	t.Run("advanced recommendations answer with the same response type", func(t *testing.T) {
		c := browser()
		if must(c.Recommendations.Popular(bg, PopularOptions{adv(2)})).RecommendationID == "" {
			t.Error("popular")
		}
		similar := must(c.Recommendations.Similar(bg, SimilarOptions{ItemID: "SKU-123", AdvancedOptions: adv(2)}))
		if similar.Strategy != "content" || similar.Items[0].ItemID == "" {
			t.Errorf("%+v", similar)
		}
		alpha := 0.3
		if must(c.Recommendations.Hybrid(bg, HybridOptions{UserID: "user_123", ItemID: "SKU-123", Alpha: &alpha, AdvancedOptions: adv(2)})).RecommendationID == "" {
			t.Error("hybrid")
		}
		if got := must(c.Recommendations.Session(bg, SessionOptions{ViewedItemIDs: []string{"SKU-123", "SKU-A"}, AdvancedOptions: adv(2)})).Strategy; got != "session" {
			t.Errorf("session list -> %s", got)
		}
		if must(c.Recommendations.Session(bg, SessionOptions{UserID: "user_123", AdvancedOptions: adv(2)})).RecommendationID == "" {
			t.Error("session for user")
		}
		_, err := c.Recommendations.Collaborative(bg, CollaborativeOptions{UserID: "user_123", AdvancedOptions: adv(2)})
		wantErr[*NotFoundError](t, err) // no trained model yet
	})

	t.Run("the public key cannot manage the catalog or users; a wrong key is refused", func(t *testing.T) {
		_, err := browser().Items.Upsert(bg, "x", ItemInput{Title: "t"})
		wantErr[*PermissionDeniedError](t, err)
		_, err = browser().Items.List(bg, ListOptions{})
		wantErr[*PermissionDeniedError](t, err)
		_, err = browser().Users.Get(bg, "user_123")
		wantErr[*PermissionDeniedError](t, err)
		bad, _ := New("nope", WithBaseURL(baseURL))
		_, err = bad.Events.View(bg, EventInput{UserID: "u", ItemID: "i"})
		wantErr[*AuthenticationError](t, err)
	})

	t.Run("API-side validation surfaces as ValidationError with the request id", func(t *testing.T) {
		_, err := server().Items.List(bg, ListOptions{Limit: 5000})
		v := wantErr[*ValidationError](t, err)
		if v.API == nil || v.API.StatusCode != 422 || !strings.HasPrefix(v.API.RequestID, "req_") {
			t.Fatalf("%+v", v)
		}
		if !errors.Is(err, v.API) {
			t.Error("the API error should be reachable with errors.Is")
		}
	})

	t.Run("cleanup", func(t *testing.T) {
		c := server()
		must(c.Items.DeleteMany(bg, []string{"SKU-123", gid, "3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "SKU-A"}))
		if err := c.Users.Delete(bg, "user_123"); err != nil {
			t.Fatal(err)
		}
	})
}
