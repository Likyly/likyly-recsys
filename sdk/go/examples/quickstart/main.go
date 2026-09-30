// Quick start: catalog -> events -> recommendations -> attribution.
//
// Run it: LIKYLY_SECRET_KEY=... LIKYLY_PUBLIC_KEY=... go run ./examples/quickstart
package main

// region:imports
import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/likyly/likyly-go"
)

// endregion

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	// region:initialize
	ctx := context.Background()

	// Backend only: the secret key gives access to your catalog and your users.
	client, err := likyly.New(os.Getenv("LIKYLY_SECRET_KEY"),
		likyly.WithBaseURL(envOr("LIKYLY_BASE_URL", likyly.DefaultBaseURL)), // docs:omit
		likyly.WithCatalog(os.Getenv("LIKYLY_CATALOG")),                     // docs:omit
	)
	if err != nil {
		log.Fatal(err)
	}
	// endregion

	// region:catalog
	// Send your catalog once (and whenever it changes). Ids are YOUR ids: SKUs, UUIDs, Shopify gids...
	_, err = client.Items.UpsertMany(ctx, []likyly.ItemImport{
		{ItemID: "SKU-1", Title: "Nike Air Max", Description: "Running shoe with air cushioning", Properties: likyly.Properties{"category": "shoes", "brand": "Nike", "price": 129.9}},
		{ItemID: "SKU-2", Title: "Adidas Ultraboost", Description: "Responsive running shoe", Properties: likyly.Properties{"category": "shoes", "brand": "Adidas", "price": 149}},
		{ItemID: "SKU-3", Title: "Nike Pegasus", Description: "Everyday running shoe", Properties: likyly.Properties{"category": "shoes", "brand": "Nike", "price": 119}},
	})
	if err != nil {
		log.Fatal(err)
	}

	// Users are optional: describe them if you want the profile to travel with their events.
	_, err = client.Users.Upsert(ctx, "user_123", likyly.UserInput{Properties: likyly.Properties{"country": "FR", "segment": "premium"}})
	if err != nil {
		log.Fatal(err)
	}
	// endregion

	// region:track
	// Tell LIKYLY what your visitors do.
	if _, err = client.Events.View(ctx, likyly.EventInput{UserID: "user_123", ItemID: "SKU-1"}); err != nil {
		log.Fatal(err)
	}

	// Purchases carry an EventID: replaying the call can never count the sale twice.
	_, err = client.Events.Purchase(ctx, likyly.EventInput{
		EventID:    "purchase_order_9281_SKU-1",
		UserID:     "user_123",
		ItemID:     "SKU-1",
		Quantity:   1,
		Properties: likyly.Properties{"price": 129.9, "currency": "EUR", "orderId": "order_9281"},
	})
	if err != nil {
		log.Fatal(err)
	}
	// endregion

	// region:recommend
	// Ask what to show. You never pick an algorithm: LIKYLY chooses the best strategy for what it knows.
	recs, err := client.Recommendations.Get(ctx, likyly.RecommendationRequest{UserID: "user_123", Placement: "homepage", Limit: 3})
	if err != nil {
		log.Fatal(err)
	}

	fmt.Println("strategy:", recs.Strategy)
	for _, item := range recs.Items {
		fmt.Println(item.ItemID, item.Title)
	}
	// endregion

	// region:showcase
	// Les recommandations pour votre page : tout est optionnel, envoyez ce que vous savez.
	productRecs, err := client.Recommendations.Get(ctx, likyly.RecommendationRequest{
		UserID:    "user_123",     // visiteur connecté
		SessionID: "sess_abc",     // ou visiteur anonyme (cookie)
		ItemID:    "SKU-1",        // fiche produit en cours de consultation
		Placement: "product_page", // où elles seront affichées (libre)
		Limit:     6,              // combien d'articles (10 par défaut)
	})
	if err != nil {
		log.Fatal(err)
	}

	for _, item := range productRecs.Items {
		fmt.Println(item.ItemID, item.Title, *item.Score)
	}
	// endregion

	// region:attribution
	// Send the RecommendationID back: LIKYLY measures which recommendations get seen, clicked and bought.
	shown := likyly.EventInput{UserID: "user_123", ItemID: recs.Items[0].ItemID, RecommendationID: recs.RecommendationID, Placement: "homepage"}
	if _, err = client.Events.Impression(ctx, shown); err != nil {
		log.Fatal(err)
	}
	if _, err = client.Events.Click(ctx, shown); err != nil {
		log.Fatal(err)
	}
	// endregion

	// region:browser
	// Where the code is exposed to visitors, use the PUBLIC key: it can only read recommendations and record events.
	front, err := likyly.New(os.Getenv("LIKYLY_PUBLIC_KEY"),
		likyly.WithBaseURL(envOr("LIKYLY_BASE_URL", likyly.DefaultBaseURL)), // docs:omit
		likyly.WithCatalog(os.Getenv("LIKYLY_CATALOG")),                     // docs:omit
	)
	if err != nil {
		log.Fatal(err)
	}

	// A visitor who is not logged in: use an anonymous session id (any string you generate and keep in a cookie).
	if _, err = front.Events.View(ctx, likyly.EventInput{SessionID: "sess_abc", ItemID: "SKU-2"}); err != nil {
		log.Fatal(err)
	}
	forVisitor, err := front.Recommendations.Get(ctx, likyly.RecommendationRequest{SessionID: "sess_abc", ViewedItemIDs: []string{"SKU-2"}, Placement: "product_page", Limit: 3})
	if err != nil {
		log.Fatal(err)
	}
	// endregion

	// region:custom
	// Any string is a valid event type: track what matters to your business.
	_, err = client.Events.Track(ctx, "favorite", likyly.EventInput{UserID: "user_123", ItemID: "SKU-2", Properties: likyly.Properties{"list": "wishlist"}})
	if err != nil {
		log.Fatal(err)
	}

	// Up to 1000 events per call, each with its own type.
	_, err = client.Events.TrackMany(ctx, []likyly.TypedEvent{
		{Type: "view", EventInput: likyly.EventInput{UserID: "user_123", ItemID: "SKU-3"}},
		{Type: "add_to_cart", EventInput: likyly.EventInput{UserID: "user_123", ItemID: "SKU-3", Quantity: 1}},
	})
	if err != nil {
		log.Fatal(err)
	}
	// endregion

	// region:advanced
	// Advanced Recommendations: one strategy at a time, when you want to choose.
	alpha := 0.7
	similar, err := client.Recommendations.Similar(ctx, likyly.SimilarOptions{ItemID: "SKU-1", AdvancedOptions: likyly.AdvancedOptions{Limit: 3}})
	if err != nil {
		log.Fatal(err)
	}
	hybrid, err := client.Recommendations.Hybrid(ctx, likyly.HybridOptions{UserID: "user_123", ItemID: "SKU-1", Alpha: &alpha, AdvancedOptions: likyly.AdvancedOptions{Limit: 3}})
	if err != nil {
		log.Fatal(err)
	}
	session, err := client.Recommendations.Session(ctx, likyly.SessionOptions{ViewedItemIDs: []string{"SKU-1", "SKU-2"}, AdvancedOptions: likyly.AdvancedOptions{Limit: 3}})
	if err != nil {
		log.Fatal(err)
	}
	// endregion
	_, _, _ = similar, hybrid, session

	// region:config
	tuned, err := likyly.New(os.Getenv("LIKYLY_SECRET_KEY"),
		likyly.WithTimeout(5*time.Second),                                   // per attempt (default 10s)
		likyly.WithMaxRetries(3),                                            // automatic retries on 429 / 502 / 503 / 504 / network errors (default 2, 0 disables)
		likyly.WithUserAgent("my-shop/1.4"),                                 // appended to the SDK's User-Agent
		likyly.WithBaseURL(envOr("LIKYLY_BASE_URL", likyly.DefaultBaseURL)), // docs:omit
	)
	if err != nil {
		log.Fatal(err)
	}
	// endregion
	_ = tuned

	// region:errors
	_, err = client.Items.Get(ctx, "does-not-exist")
	var notFound *likyly.NotFoundError
	var rateLimited *likyly.RateLimitError
	switch {
	case errors.As(err, &notFound):
		fmt.Println("no such item, request", notFound.RequestID)
	case errors.As(err, &rateLimited):
		fmt.Println("slow down, retry in", rateLimited.RetryAfter)
	case err != nil:
		log.Fatal(err)
	}
	// endregion

	fmt.Println("visitor strategy:", forVisitor.Strategy)

	// region:cleanup
	if _, err = client.Items.DeleteMany(ctx, []string{"SKU-1", "SKU-2", "SKU-3"}); err != nil {
		log.Fatal(err)
	}
	if err = client.Users.Delete(ctx, "user_123"); err != nil {
		log.Fatal(err)
	}
	// endregion
	fmt.Println("quickstart ok")
}
