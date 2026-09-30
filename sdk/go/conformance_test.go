package likyly

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

// Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK runs, and
// that validate_against_openapi.py checks against the OpenAPI document.

type scenario struct {
	ID   string `json:"id"`
	Call struct {
		Resource string            `json:"resource"`
		Method   string            `json:"method"`
		Args     []json.RawMessage `json:"args"`
	} `json:"call"`
	Request struct {
		Method string            `json:"method"`
		Path   string            `json:"path"`
		Query  map[string]string `json:"query"`
		Body   json.RawMessage   `json:"body"`
	} `json:"request"`
	Response struct {
		Status  int               `json:"status"`
		Headers map[string]string `json:"headers"`
		Body    json.RawMessage   `json:"body"`
	} `json:"response"`
	Expect map[string]any `json:"expect"`
	Config struct {
		Catalog string `json:"catalog"`
	} `json:"config"`
	Error *struct {
		Class      string   `json:"class"`
		StatusCode int      `json:"statusCode"`
		RequestID  string   `json:"requestId"`
		RetryAfter *float64 `json:"retryAfter"`
		Message    string   `json:"message"`
	} `json:"error"`
}

type scenarioFile struct {
	Defaults struct {
		APIKey          string `json:"apiKey"`
		BaseURL         string `json:"baseUrl"`
		UserAgentPrefix string `json:"userAgentPrefix"`
	} `json:"defaults"`
	Scenarios []scenario `json:"scenarios"`
}

func loadScenarios(t *testing.T) scenarioFile {
	t.Helper()
	raw, err := os.ReadFile("../conformance/scenarios.json")
	if err != nil {
		t.Fatal(err)
	}
	var f scenarioFile
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func upperFirst(s string) string { return strings.ToUpper(s[:1]) + s[1:] }

// invoke calls client.<Resource>.<Method>(ctx, args...) by reflection: each JSON arg is decoded into the
// parameter's Go type (whose json tags are the public camelCase names), missing ones are zero values.
func invoke(t *testing.T, client *Client, sc scenario) (any, error) {
	t.Helper()
	svc := reflect.ValueOf(client).Elem().FieldByName(upperFirst(sc.Call.Resource))
	if !svc.IsValid() {
		t.Fatalf("no such resource %q", sc.Call.Resource)
	}
	m := svc.MethodByName(upperFirst(sc.Call.Method))
	if !m.IsValid() {
		t.Fatalf("no such method %s.%s", sc.Call.Resource, sc.Call.Method)
	}
	mt := m.Type()
	n := mt.NumIn()
	if mt.IsVariadic() {
		n--
	}
	in := []reflect.Value{reflect.ValueOf(context.Background())}
	for i := 1; i < n; i++ {
		p := reflect.New(mt.In(i))
		if i-1 < len(sc.Call.Args) {
			if err := json.Unmarshal(sc.Call.Args[i-1], p.Interface()); err != nil {
				t.Fatalf("arg %d: %v", i-1, err)
			}
		}
		in = append(in, p.Elem())
	}
	out := m.Call(in)
	var err error
	if e := out[len(out)-1]; !e.IsNil() {
		err = e.Interface().(error)
	}
	if len(out) == 1 {
		return nil, err
	}
	if out[0].IsNil() {
		return nil, err
	}
	return out[0].Interface(), err
}

func pick(t *testing.T, result any, path string) any {
	t.Helper()
	raw, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	var cur any
	_ = json.Unmarshal(raw, &cur)
	for _, key := range strings.Split(path, ".") {
		switch v := cur.(type) {
		case nil:
			return nil
		case []any:
			if key == "length" {
				return float64(len(v))
			}
			i, err := strconv.Atoi(key)
			if err != nil || i >= len(v) {
				return nil
			}
			cur = v[i]
		case map[string]any:
			cur = v[key] // `properties`: the developer's own keys, never renamed
		default:
			return nil
		}
	}
	return cur
}

func TestConformance(t *testing.T) {
	file := loadScenarios(t)
	if len(file.Scenarios) == 0 {
		t.Fatal("no scenarios loaded")
	}
	for _, sc := range file.Scenarios {
		sc := sc
		t.Run(sc.ID, func(t *testing.T) {
			st := step{status: sc.Response.Status, headers: sc.Response.Headers}
			if len(sc.Response.Body) > 0 && string(sc.Response.Body) != "null" {
				st.raw = string(sc.Response.Body)
			}
			opts := []Option{WithBaseURL(file.Defaults.BaseURL), WithMaxRetries(0)}
			if sc.Config.Catalog != "" {
				opts = append(opts, WithCatalog(sc.Config.Catalog))
			}
			h := newHarness(t, []step{st}, opts...)

			result, err := invoke(t, h.client, sc)

			if sc.Error != nil {
				checkError(t, sc, err)
			} else {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				for path, want := range sc.Expect {
					if got := pick(t, result, path); !reflect.DeepEqual(got, want) {
						t.Errorf("result.%s = %#v, want %#v", path, got, want)
					}
				}
			}
			assertRequest(t, file, sc, h.fake)
		})
	}
}

func checkError(t *testing.T, sc scenario, err error) {
	t.Helper()
	if err == nil {
		t.Fatal("expected an error")
	}
	want := sc.Error
	class := strings.TrimPrefix(reflect.TypeOf(err).String(), "*likyly.")
	if class != strings.Replace(want.Class, "ApiError", "APIError", 1) {
		t.Fatalf("error type = %s, want %s (%v)", class, want.Class, err)
	}
	api, ok := errAs[*APIError](err)
	if !ok {
		t.Fatalf("%T does not expose an *APIError", err)
	}
	if api.StatusCode != want.StatusCode {
		t.Errorf("StatusCode = %d, want %d", api.StatusCode, want.StatusCode)
	}
	if want.RequestID != "" && api.RequestID != want.RequestID {
		t.Errorf("RequestID = %q, want %q", api.RequestID, want.RequestID)
	}
	if want.RetryAfter != nil && api.RetryAfter.Seconds() != *want.RetryAfter {
		t.Errorf("RetryAfter = %v, want %vs", api.RetryAfter, *want.RetryAfter)
	}
	if want.Message != "" && api.Message != want.Message {
		t.Errorf("Message = %q, want %q", api.Message, want.Message)
	}
}

func assertRequest(t *testing.T, file scenarioFile, sc scenario, f *fakeDoer) {
	t.Helper()
	if len(f.calls) != 1 {
		t.Fatalf("%d HTTP requests, want exactly 1", len(f.calls))
	}
	got, want := f.calls[0], sc.Request
	if got.method != want.Method {
		t.Errorf("method = %s, want %s", got.method, want.Method)
	}
	if origin := got.url.Scheme + "://" + got.url.Host; origin != file.Defaults.BaseURL {
		t.Errorf("origin = %s, want %s", origin, file.Defaults.BaseURL)
	}
	if got.url.EscapedPath() != want.Path {
		t.Errorf("raw path = %s, want %s", got.url.EscapedPath(), want.Path)
	}
	query := map[string]string{}
	for k, v := range got.url.Query() {
		query[k] = v[0]
	}
	if !reflect.DeepEqual(query, orEmpty(want.Query)) {
		t.Errorf("query = %v, want %v", query, want.Query)
	}
	if k := got.header.Get("X-API-Key"); k != file.Defaults.APIKey {
		t.Errorf("X-API-Key = %q", k)
	}
	if ua := got.header.Get("User-Agent"); !strings.HasPrefix(ua, file.Defaults.UserAgentPrefix) {
		t.Errorf("User-Agent = %q", ua)
	}
	wantBody := len(want.Body) > 0
	if ct := got.header.Get("Content-Type"); (ct == "application/json") != wantBody || (!wantBody && ct != "") {
		t.Errorf("Content-Type = %q (body expected: %v)", ct, wantBody)
	}
	if wantBody {
		var g, w any
		if err := json.Unmarshal(got.body, &g); err != nil {
			t.Fatalf("request body: %v", err)
		}
		_ = json.Unmarshal(want.Body, &w)
		if !reflect.DeepEqual(g, w) {
			t.Errorf("body = %s, want %s", got.body, want.Body)
		}
	} else if len(got.body) != 0 {
		t.Errorf("unexpected body %s", got.body)
	}
}

func orEmpty(m map[string]string) map[string]string {
	if m == nil {
		return map[string]string{}
	}
	return m
}

var _ = errors.New
var _ = http.MethodGet
