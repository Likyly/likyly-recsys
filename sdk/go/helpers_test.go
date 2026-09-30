package likyly

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

// step scripts one HTTP outcome. The last step repeats once the script is exhausted.
type step struct {
	status  int // default 200
	headers map[string]string
	body    any    // marshalled to JSON
	raw     string // sent as is (wins over body)
	fail    error  // the transport fails
	hang    bool   // block until the request's context ends
}

type recorded struct {
	method string
	url    *url.URL
	header http.Header
	body   []byte
}

func (r recorded) jsonBody(t *testing.T) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(r.body, &m); err != nil {
		t.Fatalf("request body is not a JSON object: %v (%q)", err, r.body)
	}
	return m
}

type fakeDoer struct {
	mu    sync.Mutex
	steps []step
	calls []recorded
}

func (f *fakeDoer) Do(req *http.Request) (*http.Response, error) {
	var body []byte
	if req.Body != nil {
		body, _ = io.ReadAll(req.Body)
	}
	f.mu.Lock()
	f.calls = append(f.calls, recorded{method: req.Method, url: req.URL, header: req.Header.Clone(), body: body})
	i := len(f.calls) - 1
	if i >= len(f.steps) {
		i = len(f.steps) - 1
	}
	s := f.steps[i]
	f.mu.Unlock()

	if s.hang {
		<-req.Context().Done()
		return nil, req.Context().Err()
	}
	if s.fail != nil {
		return nil, s.fail
	}
	status := s.status
	if status == 0 {
		status = 200
	}
	payload := s.raw
	if payload == "" && s.body != nil {
		b, _ := json.Marshal(s.body)
		payload = string(b)
	}
	h := http.Header{}
	for k, v := range s.headers {
		h.Set(k, v)
	}
	return &http.Response{StatusCode: status, Header: h, Body: io.NopCloser(strings.NewReader(payload)), Request: req}, nil
}

func (f *fakeDoer) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

type harness struct {
	client *Client
	fake   *fakeDoer
	sleeps []time.Duration
}

// newHarness builds a client over a scripted transport: no real waiting, jitter pinned to 1 (so a backoff is
// exactly base * 2^attempt).
func newHarness(t *testing.T, steps []step, opts ...Option) *harness {
	t.Helper()
	h := &harness{fake: &fakeDoer{steps: steps}}
	all := append([]Option{
		WithBaseURL("https://api.example.test"),
		WithHTTPClient(h.fake),
		withSleep(func(_ context.Context, d time.Duration) error { h.sleeps = append(h.sleeps, d); return nil }),
		withRandom(func() float64 { return 1 }),
	}, opts...)
	c, err := New("sk_test_conformance", all...)
	if err != nil {
		t.Fatal(err)
	}
	h.client = c
	return h
}

// errAs is errors.As without the boilerplate.
func errAs[T error](err error) (T, bool) {
	var target T
	ok := errors.As(err, &target)
	return target, ok
}
