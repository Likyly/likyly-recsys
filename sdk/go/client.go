// Package likyly is the official Go client for the LIKYLY recommendations API.
//
//	client, err := likyly.New(os.Getenv("LIKYLY_SECRET_KEY"))
//	if err != nil { ... }
//
//	// Track what a visitor does
//	_, err = client.Events.View(ctx, likyly.EventInput{UserID: "user_123", ItemID: "SKU-456"})
//
//	// Ask what to show them
//	recs, err := client.Recommendations.Get(ctx, likyly.RecommendationRequest{UserID: "user_123", Placement: "homepage", Limit: 8})
//
// There are two kinds of API key. The secret key is for your backend only (items, users, everything). The
// public key is safe to expose in a web page but can only ask for recommendations and record events.
// Never ship the secret key in code that runs on a visitor's device.
package likyly

import (
	"context"
	"net/http"
	"strings"
	"time"
)

// Client is the LIKYLY client. Four things to know:
//
//	client.Items            your catalog
//	client.Users            your users (optional)
//	client.Events           what visitors do
//	client.Recommendations  what to show them
//
// A Client is safe for concurrent use.
type Client struct {
	Items           *ItemsService
	Users           *UsersService
	Events          *EventsService
	Recommendations *RecommendationsService
}

type config struct {
	baseURL    string
	catalog    string
	timeout    time.Duration
	maxRetries int
	userAgent  string
	doer       Doer
	sleep      func(context.Context, time.Duration) error
	random     func() float64
}

// Option configures New.
type Option func(*config)

// WithBaseURL points the client somewhere other than https://api.likyly.com (a proxy, a local API).
func WithBaseURL(u string) Option { return func(c *config) { c.baseURL = u } }

// WithCatalog selects which of your catalogs to use, if your account has several. Omit it if you have one:
// LIKYLY uses your only catalog.
func WithCatalog(name string) Option { return func(c *config) { c.catalog = name } }

// WithTimeout sets the per-attempt request timeout. Default 10s.
func WithTimeout(d time.Duration) Option { return func(c *config) { c.timeout = d } }

// WithMaxRetries sets the automatic retries on transient failures (429, 502, 503, 504, dropped
// connections). Default 2. Use 0 to disable.
func WithMaxRetries(n int) Option { return func(c *config) { c.maxRetries = n } }

// WithUserAgent appends a suffix to the SDK's User-Agent, e.g. "my-shop/1.4".
func WithUserAgent(s string) Option { return func(c *config) { c.userAgent = s } }

// WithHTTPClient uses your own *http.Client (proxies, custom transports, tracing) or any Doer.
func WithHTTPClient(d Doer) Option { return func(c *config) { c.doer = d } }

// New creates a client. apiKey is required.
func New(apiKey string, opts ...Option) (*Client, error) {
	if strings.TrimSpace(apiKey) == "" {
		return nil, validationf("apiKey is required (create one in your LIKYLY account)")
	}
	cfg := config{
		baseURL:    DefaultBaseURL,
		timeout:    10 * time.Second,
		maxRetries: 2,
		sleep:      defaultSleep,
		random:     defaultRandom,
	}
	for _, o := range opts {
		o(&cfg)
	}
	if cfg.doer == nil {
		cfg.doer = http.DefaultClient
	}
	ua := userAgent
	if cfg.userAgent != "" {
		ua += " " + cfg.userAgent
	}
	c := &core{
		doer:       cfg.doer,
		apiKey:     apiKey,
		baseURL:    strings.TrimRight(cfg.baseURL, "/"),
		catalog:    cfg.catalog,
		timeout:    cfg.timeout,
		maxRetries: cfg.maxRetries,
		userAgent:  ua,
		sleep:      cfg.sleep,
		random:     cfg.random,
	}
	return &Client{
		Items:           &ItemsService{c},
		Users:           &UsersService{c},
		Events:          &EventsService{c},
		Recommendations: &RecommendationsService{c},
	}, nil
}

// withSleep and withRandom are test hooks (backoff without waiting, deterministic jitter).
func withSleep(f func(context.Context, time.Duration) error) Option {
	return func(c *config) { c.sleep = f }
}
func withRandom(f func() float64) Option { return func(c *config) { c.random = f } }
