package likyly

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	retryBase = 500 * time.Millisecond
	retryCap  = 8 * time.Second
	// A Retry-After longer than this is not waited for: the RateLimitError is returned instead.
	maxRetryAfter = 60 * time.Second
)

// Doer is what the SDK needs from an HTTP client; *http.Client satisfies it.
type Doer interface {
	Do(req *http.Request) (*http.Response, error)
}

// CallOption tunes a single call (the last, variadic argument of every method).
type CallOption func(*callConfig)

type callConfig struct {
	timeout    time.Duration
	maxRetries *int
}

// WithRequestTimeout overrides the client's per-attempt timeout for this call.
func WithRequestTimeout(d time.Duration) CallOption {
	return func(c *callConfig) { c.timeout = d }
}

// WithRequestMaxRetries overrides the client's retry count for this call (0 disables retries).
func WithRequestMaxRetries(n int) CallOption {
	return func(c *callConfig) { c.maxRetries = &n }
}

// core is the only place that talks HTTP. Every resource goes through request: auth header, catalog
// parameter, timeout, retries with exponential backoff + jitter, and the error mapping.
type core struct {
	doer       Doer
	apiKey     string
	baseURL    string
	catalog    string
	timeout    time.Duration
	maxRetries int
	userAgent  string
	sleep      func(context.Context, time.Duration) error
	random     func() float64
}

type response struct {
	headers map[string]string // keys lower-cased
	raw     []byte
}

type queryParam struct{ key, value string }

type call struct {
	method, path string
	query        []queryParam
	body         map[string]any
	// idempotent says whether the call is safe to send again if its outcome is unknown (a timeout, a 5xx, a
	// dropped connection): false for events without an EventID, where a blind retry could record the event twice.
	idempotent bool
	opts       []CallOption
}

func (c *core) request(ctx context.Context, cl call) (*response, error) {
	cfg := callConfig{timeout: c.timeout}
	for _, o := range cl.opts {
		o(&cfg)
	}
	maxRetries := c.maxRetries
	if cfg.maxRetries != nil {
		maxRetries = *cfg.maxRetries
	}

	target := c.buildURL(cl.path, cl.query)
	var payload []byte
	if cl.body != nil {
		var err error
		if payload, err = json.Marshal(cl.body); err != nil {
			return nil, validationf("request body is not valid JSON: %v", err)
		}
	}
	for attempt := 0; ; attempt++ {
		resp, err := c.once(ctx, cl.method, target, payload, cfg.timeout)
		if err == nil {
			return resp, nil
		}
		delay, retry := c.retryDelay(err, attempt, cl.idempotent)
		if !retry || attempt >= maxRetries {
			return nil, err
		}
		if serr := c.sleep(ctx, delay); serr != nil {
			return nil, serr
		}
	}
}

func (c *core) retryDelay(err error, attempt int, idempotent bool) (time.Duration, bool) {
	backoff := time.Duration(c.random() * math.Min(float64(retryCap), float64(retryBase)*math.Pow(2, float64(attempt)))) // full jitter
	var api *APIError
	if errors.As(err, &api) {
		afterHeader := func() (time.Duration, bool) {
			if api.hasRetryAfter {
				return api.RetryAfter, api.RetryAfter <= maxRetryAfter
			}
			return backoff, true
		}
		if api.StatusCode == 429 {
			// Rejected by the rate limiter before reaching the application: nothing was processed, so retrying is safe for every request.
			return afterHeader()
		}
		if !idempotent {
			return 0, false // the outcome is unknown - never risk a duplicate
		}
		if api.StatusCode == 502 || api.StatusCode == 503 || api.StatusCode == 504 {
			return afterHeader()
		}
		return 0, false
	}
	if !idempotent {
		return 0, false
	}
	var netErr *NetworkError
	var timeoutErr *TimeoutError
	if errors.As(err, &netErr) || errors.As(err, &timeoutErr) {
		return backoff, true
	}
	return 0, false
}

func (c *core) buildURL(path string, query []queryParam) string {
	params := append([]queryParam{}, query...)
	if c.catalog != "" {
		params = append(params, queryParam{"data_product_type", c.catalog})
	}
	var sb strings.Builder
	sb.WriteString(c.baseURL)
	sb.WriteString(path)
	sep := byte('?')
	for _, p := range params {
		sb.WriteByte(sep)
		sb.WriteString(url.QueryEscape(p.key))
		sb.WriteByte('=')
		sb.WriteString(url.QueryEscape(p.value))
		sep = '&'
	}
	return sb.String()
}

func (c *core) once(parent context.Context, method, target string, payload []byte, timeout time.Duration) (*response, error) {
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()

	var reader io.Reader
	if payload != nil {
		reader = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(ctx, method, target, reader)
	if err != nil {
		return nil, validationf("invalid request URL: %v", err)
	}
	req.Header.Set("X-API-Key", c.apiKey)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.doer.Do(req)
	if err != nil {
		return nil, c.transportError(parent, err, timeout)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, c.transportError(parent, err, timeout)
	}
	headers := map[string]string{}
	for k, v := range resp.Header {
		headers[strings.ToLower(k)] = strings.Join(v, ", ")
	}

	if resp.StatusCode >= 400 {
		var data any
		if len(raw) > 0 {
			if json.Unmarshal(raw, &data) != nil {
				data = string(raw) // e.g. an HTML 502 page from a proxy
			}
		}
		requestID := headers["x-request-id"]
		if m, ok := data.(map[string]any); ok && requestID == "" && m["request_id"] != nil {
			requestID = asString(m["request_id"])
		}
		retryAfter, hasRetryAfter := parseRetryAfter(headers["retry-after"])
		return nil, newAPIError(resp.StatusCode, data, requestID, retryAfter, hasRetryAfter)
	}
	return &response{headers: headers, raw: raw}, nil
}

// transportError: the caller's own cancellation is surfaced untouched; the client's timeout becomes a TimeoutError.
func (c *core) transportError(parent context.Context, err error, timeout time.Duration) error {
	if parent.Err() != nil {
		return parent.Err()
	}
	var ne net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &ne) && ne.Timeout()) {
		return &TimeoutError{Timeout: timeout, Err: err}
	}
	return &NetworkError{Err: err}
}

// decode unmarshals a successful response body into out.
func (r *response) decode(out any) error {
	if err := json.Unmarshal(r.raw, out); err != nil {
		return &APIError{Message: "the API answered with a body that is not the expected JSON: " + err.Error()}
	}
	return nil
}

func asString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	b, _ := json.Marshal(v)
	return string(b)
}

// parseRetryAfter reads delta-seconds or an HTTP date; the bool says whether the header was usable.
func parseRetryAfter(value string) (time.Duration, bool) {
	if value == "" {
		return 0, false
	}
	if secs, err := strconv.ParseFloat(value, 64); err == nil && secs >= 0 {
		return time.Duration(secs * float64(time.Second)), true
	}
	if t, err := http.ParseTime(value); err == nil {
		if d := time.Until(t); d > 0 {
			return d, true
		}
		return 0, true
	}
	return 0, false
}

func defaultSleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func defaultRandom() float64 { return rand.Float64() }
