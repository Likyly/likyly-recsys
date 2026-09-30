package likyly

import (
	"context"
	"errors"
	"net/http"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"
)

var (
	recBody   = map[string]any{"recommendation_id": "rec_1", "strategy": "popular", "items": []any{}}
	evtBody   = map[string]any{"message": "ok", "event_id": nil, "duplicate": false}
	itemBody1 = map[string]any{"item_id": "a", "title": "t"}
	ctx       = context.Background()
)

func wantErr[T error](t *testing.T, err error) T {
	t.Helper()
	target, ok := errAs[T](err)
	if !ok {
		t.Fatalf("error %T (%v) is not the expected type", err, err)
	}
	return target
}

// ---- configuration ------------------------------------------------------------------------------

func TestNewRequiresAnAPIKey(t *testing.T) {
	for _, key := range []string{"", "   "} {
		_, err := New(key)
		wantErr[*ValidationError](t, err)
	}
}

func TestDefaultsToProductionAndStripsTrailingSlashes(t *testing.T) {
	h := &fakeDoer{steps: []step{{body: []any{}}}}
	c, _ := New("k", WithHTTPClient(h))
	if _, err := c.Items.List(ctx, ListOptions{}); err != nil {
		t.Fatal(err)
	}
	if got := h.calls[0].url.Scheme + "://" + h.calls[0].url.Host; got != "https://api.likyly.com" {
		t.Errorf("default origin = %s", got)
	}

	h = &fakeDoer{steps: []step{{body: []any{}}}}
	c, _ = New("k", WithHTTPClient(h), WithBaseURL("https://proxy.example.test/likyly///"))
	_, _ = c.Items.List(ctx, ListOptions{})
	if got := h.calls[0].url.Path; got != "/likyly/items" {
		t.Errorf("path = %s", got)
	}
}

func TestUserAgentIsExtendedNotReplaced(t *testing.T) {
	h := newHarness(t, []step{{body: []any{}}}, WithUserAgent("my-shop/1.4"))
	_, _ = h.client.Items.List(ctx, ListOptions{})
	ua := h.fake.calls[0].header.Get("User-Agent")
	if !regexp.MustCompile(`^likyly-go/\d+\.\d+\.\d+ \(go[^)]*\) my-shop/1\.4$`).MatchString(ua) {
		t.Errorf("User-Agent = %q", ua)
	}
}

func TestAPIKeyGoesInTheHeaderNeverTheURL(t *testing.T) {
	h := newHarness(t, []step{{body: []any{}}})
	_, _ = h.client.Items.List(ctx, ListOptions{})
	c := h.fake.calls[0]
	if c.header.Get("X-API-Key") != "sk_test_conformance" || strings.Contains(c.url.String(), "sk_test") {
		t.Errorf("key leaked or missing: %v %s", c.header, c.url)
	}
}

// ---- validation: nothing is sent ------------------------------------------------------------------

func TestRequestValidationHappensBeforeAnythingIsSent(t *testing.T) {
	h := newHarness(t, []step{{body: evtBody}})
	c := h.client
	for name, err := range map[string]error{
		"event without a user or a session": second(c.Events.View(ctx, EventInput{ItemID: "a"})),
		"event without an item":             second(c.Events.View(ctx, EventInput{UserID: "u"})),
		"blank item id":                     second(c.Events.View(ctx, EventInput{ItemID: "  ", UserID: "u"})),
		"blank item id on get":              second(c.Items.Get(ctx, "")),
		"item without a title":              second(c.Items.Upsert(ctx, "a", ItemInput{})),
		"empty batch":                       second(c.Items.UpsertMany(ctx, nil)),
		"empty delete batch":                second(c.Items.DeleteMany(ctx, nil)),
		"empty user batch":                  second(c.Users.Import(ctx, nil)),
		"empty event batch":                 second(c.Events.TrackMany(ctx, nil)),
		"bad event type":                    second(c.Events.Track(ctx, "bad type!", EventInput{UserID: "u", ItemID: "i"})),
		"negative limit":                    second(c.Recommendations.Popular(ctx, PopularOptions{AdvancedOptions{Limit: -1}})),
		"session with nothing":              second(c.Recommendations.Session(ctx, SessionOptions{})),
		"session with both":                 second(c.Recommendations.Session(ctx, SessionOptions{ViewedItemIDs: []string{"a"}, UserID: "u"})),
		"session with an empty list":        second(c.Recommendations.Session(ctx, SessionOptions{ViewedItemIDs: []string{}})),
		"session id with a comma":           second(c.Recommendations.Session(ctx, SessionOptions{ViewedItemIDs: []string{"a,b"}})),
	} {
		if _, ok := errAs[*ValidationError](err); !ok {
			t.Errorf("%s: got %v, want *ValidationError", name, err)
		}
	}
	if n := h.fake.count(); n != 0 {
		t.Errorf("%d requests were sent", n)
	}
}

func second[T any](_ T, err error) error { return err }

func TestEventTypesAreOpenStringsButURLSafe(t *testing.T) {
	h := newHarness(t, []step{{body: evtBody}})
	if _, err := h.client.Events.Track(ctx, "favorite", EventInput{UserID: "u", ItemID: "i"}); err != nil {
		t.Fatal(err)
	}
	if got := h.fake.calls[0].url.Path; got != "/events/favorite" {
		t.Errorf("path = %s", got)
	}
}

func TestAdvancedEndpointsRefuseAnIDContainingASlash(t *testing.T) {
	h := newHarness(t, []step{{body: recBody}})
	_, err := h.client.Recommendations.Similar(ctx, SimilarOptions{ItemID: "gid://shopify/Product/1"})
	if v := wantErr[*ValidationError](t, err); !strings.Contains(v.Error(), "Recommendations.Get") {
		t.Errorf("the message should point to Get: %v", v)
	}
	// the body-based call accepts it
	if _, err := h.client.Recommendations.Get(ctx, RecommendationRequest{ItemID: "gid://shopify/Product/1"}); err != nil {
		t.Fatal(err)
	}
	if n := h.fake.count(); n != 1 {
		t.Errorf("%d requests, want 1", n)
	}
}

func TestOccurredAtIsSentAsUTCISO8601(t *testing.T) {
	h := newHarness(t, []step{{body: evtBody}})
	paris := time.FixedZone("CEST", 2*3600)
	_, _ = h.client.Events.View(ctx, EventInput{UserID: "u", ItemID: "i", OccurredAt: time.Date(2026, 9, 24, 12, 30, 0, 0, paris)})
	if got := h.fake.calls[0].jsonBody(t)["occurred_at"]; got != "2026-09-24T10:30:00Z" {
		t.Errorf("occurred_at = %v", got)
	}
}

func TestPropertiesKeysAreNeverRenamedAtAnyDepth(t *testing.T) {
	h := newHarness(t, []step{{body: evtBody}})
	props := Properties{"orderId": "O-1", "nested": map[string]any{"snakeCase_and_camelCase": 1.0}}
	_, _ = h.client.Events.Purchase(ctx, EventInput{UserID: "u", ItemID: "i", Properties: props})
	got := h.fake.calls[0].jsonBody(t)["properties"].(map[string]any)
	if got["orderId"] != "O-1" || got["nested"].(map[string]any)["snakeCase_and_camelCase"] != 1.0 {
		t.Errorf("properties = %v", got)
	}
}

func TestIDsAreEncodedAsASingleSegment(t *testing.T) {
	h := newHarness(t, []step{{body: itemBody1}})
	_, _ = h.client.Items.Get(ctx, "a b/c?d#e%f é")
	if got := h.fake.calls[0].url.EscapedPath(); got != "/items/a%20b%2Fc%3Fd%23e%25f%20%C3%A9" {
		t.Errorf("path = %s", got)
	}
}

func TestAlphaZeroIsSentNotDropped(t *testing.T) {
	h := newHarness(t, []step{{body: recBody}})
	zero := 0.0
	_, _ = h.client.Recommendations.Hybrid(ctx, HybridOptions{UserID: "u", ItemID: "i", Alpha: &zero})
	if got := h.fake.calls[0].url.Query().Get("alpha"); got != "0" {
		t.Errorf("alpha = %q", got)
	}
}

func TestDebugAndSessionsAreSentOnGet(t *testing.T) {
	h := newHarness(t, []step{{body: recBody}})
	_, _ = h.client.Recommendations.Get(ctx, RecommendationRequest{SessionID: "s", Debug: true, Limit: 3})
	b := h.fake.calls[0].jsonBody(t)
	if b["debug"] != true || b["session_id"] != "s" || b["count"] != 3.0 {
		t.Errorf("body = %v", b)
	}
}

// ---- errors -----------------------------------------------------------------------------------------

func TestErrorHierarchyIsUsable(t *testing.T) {
	h := newHarness(t, []step{{status: 401, body: map[string]any{"detail": "nope", "request_id": "req_x"}}}, WithMaxRetries(0))
	_, err := h.client.Items.Get(ctx, "a")
	auth := wantErr[*AuthenticationError](t, err)
	api := wantErr[*APIError](t, err)
	if auth.StatusCode != 401 || api.RequestID != "req_x" || api.Message != "nope" {
		t.Errorf("%+v", api)
	}
	if !strings.Contains(err.Error(), "req_x") {
		t.Errorf("the request id should be in the message: %v", err)
	}
}

func TestEachStatusMapsToItsType(t *testing.T) {
	check := func(status int, ok func(error) bool) {
		h := newHarness(t, []step{{status: status, body: map[string]any{"detail": "x"}}}, WithMaxRetries(0))
		_, err := h.client.Items.Get(ctx, "a")
		if !ok(err) {
			t.Errorf("status %d -> %T", status, err)
		}
	}
	check(403, func(e error) bool { _, ok := errAs[*PermissionDeniedError](e); return ok })
	check(404, func(e error) bool { _, ok := errAs[*NotFoundError](e); return ok })
	check(422, func(e error) bool { _, ok := errAs[*ValidationError](e); return ok })
	check(429, func(e error) bool { _, ok := errAs[*RateLimitError](e); return ok })
	check(500, func(e error) bool {
		a, ok := errAs[*APIError](e)
		return ok && a.StatusCode == 500 && reflectName(e) == "APIError"
	})
}

func reflectName(e error) string { return strings.TrimPrefix(reflect.TypeOf(e).String(), "*likyly.") }

func TestA422ListsTheOffendingFieldsInTheMessage(t *testing.T) {
	h := newHarness(t, []step{{status: 422, body: map[string]any{"detail": []any{map[string]any{"loc": []any{"body", "title"}, "msg": "Field required"}}, "request_id": "r"}}}, WithMaxRetries(0))
	_, err := h.client.Items.Get(ctx, "a")
	if !strings.Contains(err.Error(), "Field required") {
		t.Errorf("message = %v", err)
	}
}

func TestANonJSONErrorBodyStillBecomesAnAPIError(t *testing.T) {
	h := newHarness(t, []step{{status: 502, raw: "<html>Bad gateway</html>"}}, WithMaxRetries(0))
	_, err := h.client.Items.Get(ctx, "a")
	if api := wantErr[*APIError](t, err); api.StatusCode != 502 {
		t.Errorf("status = %d", api.StatusCode)
	}
}

func TestANonJSONSuccessBodyIsAnError(t *testing.T) {
	h := newHarness(t, []step{{raw: "<html>captive portal</html>"}}, WithMaxRetries(0))
	_, err := h.client.Items.Get(ctx, "a")
	wantErr[*APIError](t, err)
}

func TestNetworkFailuresBecomeNetworkErrorWithTheCause(t *testing.T) {
	cause := errors.New("connection reset")
	h := newHarness(t, []step{{fail: cause}}, WithMaxRetries(0))
	_, err := h.client.Items.Get(ctx, "a")
	wantErr[*NetworkError](t, err)
	if !errors.Is(err, cause) {
		t.Error("the cause should be reachable with errors.Is")
	}
}

func TestAHangingRequestBecomesTimeoutError(t *testing.T) {
	h := newHarness(t, []step{{hang: true}}, WithTimeout(20*time.Millisecond), WithMaxRetries(0))
	_, err := h.client.Items.Get(ctx, "a")
	wantErr[*TimeoutError](t, err)
}

func TestTheCallersOwnCancellationIsSurfacedUntouched(t *testing.T) {
	h := newHarness(t, []step{{hang: true}}, WithTimeout(5*time.Second), WithMaxRetries(2))
	c, cancel := context.WithCancel(ctx)
	go func() { time.Sleep(20 * time.Millisecond); cancel() }()
	_, err := h.client.Items.Get(c, "a")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v, want context.Canceled", err)
	}
	for _, bad := range []bool{
		func() bool { _, ok := errAs[*NetworkError](err); return ok }(),
		func() bool { _, ok := errAs[*TimeoutError](err); return ok }(),
		func() bool { _, ok := errAs[*APIError](err); return ok }(),
	} {
		if bad {
			t.Error("a caller cancellation must not be wrapped in an SDK error")
		}
	}
	if n := h.fake.count(); n != 1 {
		t.Errorf("a cancelled call must not be retried (%d requests)", n)
	}
}

// ---- retries ----------------------------------------------------------------------------------------

func TestA429IsRetriedForEveryRequestHonoringRetryAfter(t *testing.T) {
	h := newHarness(t, []step{{status: 429, headers: map[string]string{"Retry-After": "3"}, body: map[string]any{"detail": "slow down"}}, {body: evtBody}})
	res, err := h.client.Events.View(ctx, EventInput{UserID: "u", ItemID: "i"}) // no EventID: still safe, a 429 never reached the app
	if err != nil || res.Duplicate {
		t.Fatalf("%v %+v", err, res)
	}
	if h.fake.count() != 2 || len(h.sleeps) != 1 || h.sleeps[0] != 3*time.Second {
		t.Errorf("calls=%d sleeps=%v", h.fake.count(), h.sleeps)
	}
}

func TestARetryAfterOfZeroMeansRetryNow(t *testing.T) {
	h := newHarness(t, []step{{status: 429, headers: map[string]string{"Retry-After": "0"}, body: map[string]any{"detail": "x"}}, {body: itemBody1}})
	if _, err := h.client.Items.Get(ctx, "a"); err != nil {
		t.Fatal(err)
	}
	if len(h.sleeps) != 1 || h.sleeps[0] != 0 {
		t.Errorf("sleeps = %v", h.sleeps)
	}
}

func TestA429WithoutRetryAfterUsesBackoff(t *testing.T) {
	h := newHarness(t, []step{{status: 429, body: map[string]any{"detail": "x"}}, {body: itemBody1}})
	if _, err := h.client.Items.Get(ctx, "a"); err != nil {
		t.Fatal(err)
	}
	if len(h.sleeps) != 1 || h.sleeps[0] != 500*time.Millisecond {
		t.Errorf("sleeps = %v", h.sleeps)
	}
}

func TestARetryAfterBeyondAMinuteIsNotWaitedFor(t *testing.T) {
	h := newHarness(t, []step{{status: 429, headers: map[string]string{"Retry-After": "600"}, body: map[string]any{"detail": "x"}}})
	_, err := h.client.Items.Get(ctx, "a")
	if rl := wantErr[*RateLimitError](t, err); rl.RetryAfter != 600*time.Second {
		t.Errorf("RetryAfter = %v", rl.RetryAfter)
	}
	if h.fake.count() != 1 {
		t.Errorf("%d requests", h.fake.count())
	}
}

func TestExponentialBackoffWithJitterThenGivesUp(t *testing.T) {
	h := newHarness(t, []step{{status: 503, body: map[string]any{"detail": "down"}}}, WithMaxRetries(3))
	_, err := h.client.Items.Get(ctx, "a")
	wantErr[*APIError](t, err)
	if h.fake.count() != 4 {
		t.Errorf("%d requests, want 4", h.fake.count())
	}
	want := []time.Duration{500 * time.Millisecond, time.Second, 2 * time.Second} // random() pinned to 1
	if len(h.sleeps) != 3 || h.sleeps[0] != want[0] || h.sleeps[1] != want[1] || h.sleeps[2] != want[2] {
		t.Errorf("sleeps = %v, want %v", h.sleeps, want)
	}
}

func TestBackoffIsCappedAtEightSeconds(t *testing.T) {
	h := newHarness(t, []step{{status: 503, body: map[string]any{"detail": "down"}}}, WithMaxRetries(7))
	_, _ = h.client.Items.Get(ctx, "a")
	if last := h.sleeps[len(h.sleeps)-1]; last != 8*time.Second {
		t.Errorf("last backoff = %v", last)
	}
}

func TestIdempotentCallsAreRetriedOn502503504(t *testing.T) {
	for _, status := range []int{502, 503, 504} {
		h := newHarness(t, []step{{status: status, body: map[string]any{"detail": "x"}}, {body: itemBody1}})
		if _, err := h.client.Items.Upsert(ctx, "a", ItemInput{Title: "t"}); err != nil {
			t.Fatalf("status %d: %v", status, err)
		}
		if h.fake.count() != 2 {
			t.Errorf("status %d: %d requests", status, h.fake.count())
		}
		if string(h.fake.calls[0].body) != string(h.fake.calls[1].body) {
			t.Errorf("status %d: the replay must carry the same body", status)
		}
	}
}

func TestAnEventWithoutAnEventIDIsNeverRetriedOnAnAmbiguousFailure(t *testing.T) {
	for name, first := range map[string]step{
		"503":     {status: 503, body: map[string]any{"detail": "x"}},
		"network": {fail: errors.New("reset")},
		"timeout": {hang: true},
	} {
		h := newHarness(t, []step{first, {body: evtBody}}, WithTimeout(20*time.Millisecond))
		if _, err := h.client.Events.Purchase(ctx, EventInput{UserID: "u", ItemID: "i"}); err == nil {
			t.Fatalf("%s: expected an error", name)
		}
		if h.fake.count() != 1 {
			t.Errorf("%s: %d requests - a duplicate purchase could have been recorded", name, h.fake.count())
		}
	}
}

func TestAnEventWithAnEventIDIsRetried(t *testing.T) {
	h := newHarness(t, []step{{status: 503, body: map[string]any{"detail": "x"}}, {body: map[string]any{"message": "ok", "event_id": "e1", "duplicate": true}}})
	res, err := h.client.Events.Purchase(ctx, EventInput{EventID: "e1", UserID: "u", ItemID: "i"})
	if err != nil || !res.Duplicate || res.EventID != "e1" || h.fake.count() != 2 {
		t.Errorf("%v %+v calls=%d", err, res, h.fake.count())
	}
}

func TestTrackManyIsRetriedOnlyIfEveryEventHasAnEventID(t *testing.T) {
	batch := map[string]any{"received": 2, "accepted": 2, "duplicates": 0}
	down := step{status: 503, body: map[string]any{"detail": "x"}}

	a := newHarness(t, []step{down, {body: batch}})
	_, err := a.client.Events.TrackMany(ctx, []TypedEvent{
		{Type: "view", EventInput: EventInput{UserID: "u", ItemID: "1"}},
		{Type: "view", EventInput: EventInput{UserID: "u", ItemID: "2", EventID: "e"}},
	})
	if err == nil || a.fake.count() != 1 {
		t.Errorf("partially keyed batch: err=%v calls=%d", err, a.fake.count())
	}

	b := newHarness(t, []step{down, {body: batch}})
	_, err = b.client.Events.TrackMany(ctx, []TypedEvent{
		{Type: "view", EventInput: EventInput{UserID: "u", ItemID: "1", EventID: "e1"}},
		{Type: "view", EventInput: EventInput{UserID: "u", ItemID: "2", EventID: "e2"}},
	})
	if err != nil || b.fake.count() != 2 {
		t.Errorf("fully keyed batch: err=%v calls=%d", err, b.fake.count())
	}
}

func TestClientErrorsAreNeverRetried(t *testing.T) {
	for _, status := range []int{400, 401, 403, 404, 422} {
		h := newHarness(t, []step{{status: status, body: map[string]any{"detail": "x"}}, {body: map[string]any{}}})
		if _, err := h.client.Items.Get(ctx, "a"); err == nil {
			t.Fatalf("status %d: expected an error", status)
		}
		if h.fake.count() != 1 {
			t.Errorf("status %d: %d requests", status, h.fake.count())
		}
	}
}

func TestPerCallOptionsOverrideTheClients(t *testing.T) {
	h := newHarness(t, []step{{status: 503, body: map[string]any{"detail": "x"}}}, WithMaxRetries(5))
	_, _ = h.client.Items.Get(ctx, "a", WithRequestMaxRetries(0))
	if h.fake.count() != 1 {
		t.Errorf("%d requests", h.fake.count())
	}

	h = newHarness(t, []step{{hang: true}}, WithTimeout(time.Minute), WithMaxRetries(0))
	start := time.Now()
	_, err := h.client.Items.Get(ctx, "a", WithRequestTimeout(20*time.Millisecond))
	wantErr[*TimeoutError](t, err)
	if time.Since(start) > 5*time.Second {
		t.Error("the per-call timeout was ignored")
	}
}

func TestSleepingBetweenRetriesRespectsTheContext(t *testing.T) {
	cancelled, cancel := context.WithCancel(ctx)
	fake := &fakeDoer{steps: []step{{status: 503, body: map[string]any{"detail": "x"}}}}
	c, _ := New("k", WithHTTPClient(fake), WithBaseURL("https://api.example.test"),
		withSleep(func(c context.Context, d time.Duration) error { cancel(); return defaultSleep(c, time.Hour) }))
	_, err := c.Items.Get(cancelled, "a")
	if !errors.Is(err, context.Canceled) {
		t.Errorf("got %v", err)
	}
}

// ---- lists ------------------------------------------------------------------------------------------

func TestListReportsTheTotalAndThePaginationUsed(t *testing.T) {
	h := newHarness(t, []step{{body: []any{map[string]any{"item_id": "a", "title": "A"}}, headers: map[string]string{"X-Total-Count": "42"}}})
	page, err := h.client.Items.List(ctx, ListOptions{Limit: 1, Offset: 10})
	if err != nil {
		t.Fatal(err)
	}
	if page.Total == nil || *page.Total != 42 || page.Limit != 1 || page.Offset != 10 || len(page.Items) != 1 {
		t.Errorf("%+v", page)
	}
}

func TestListSendsOnlyWhatYouAskedForAndTotalIsNilWhenUnreported(t *testing.T) {
	h := newHarness(t, []step{{body: []any{}}})
	page, _ := h.client.Users.List(ctx, ListOptions{})
	if q := h.fake.calls[0].url.RawQuery; q != "" {
		t.Errorf("query = %q", q)
	}
	if page.Total != nil || len(page.Users) != 0 {
		t.Errorf("%+v", page)
	}
}

func TestZeroIsNotConfusedWithMissing(t *testing.T) {
	h := newHarness(t, []step{{body: recBody}})
	res, _ := h.client.Recommendations.Popular(ctx, PopularOptions{})
	if h.fake.calls[0].url.Path != "/getRec/popular/10" || res.Items == nil {
		t.Errorf("%s %+v", h.fake.calls[0].url.Path, res)
	}
}

var _ = http.MethodGet
