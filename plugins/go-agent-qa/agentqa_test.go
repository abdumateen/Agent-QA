package agentqa

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestClientDoJSON(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(
		writer http.ResponseWriter,
		request *http.Request,
	) {
		if request.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", request.Method)
		}
		if request.URL.Path != "/v1/example" {
			t.Errorf("path = %s, want /v1/example", request.URL.Path)
		}

		var body JSONObject
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
		}
		if body["value"] != "sample" {
			t.Errorf("value = %v, want sample", body["value"])
		}

		writer.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(writer, `{"ok":true,"count":2}`)
	}))
	defer server.Close()

	client, err := NewClient(server.URL)
	if err != nil {
		t.Fatalf("NewClient returned error: %v", err)
	}

	var result JSONObject
	err = client.DoJSON(
		context.Background(),
		http.MethodPost,
		"/v1/example",
		JSONObject{"value": "sample"},
		&result,
	)
	if err != nil {
		t.Fatalf("DoJSON returned error: %v", err)
	}
	if result["ok"] != true {
		t.Errorf("ok = %v, want true", result["ok"])
	}
	if result["count"] != float64(2) {
		t.Errorf("count = %v, want 2", result["count"])
	}
}

type temporaryConnectionError struct{}

func (temporaryConnectionError) Error() string {
	return "connection refused"
}

func (temporaryConnectionError) Timeout() bool {
	return true
}

func (temporaryConnectionError) Temporary() bool {
	return true
}

type retryTransport struct {
	attempts atomic.Int32
}

func (transport *retryTransport) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	if transport.attempts.Add(1) == 1 {
		return nil, temporaryConnectionError{}
	}
	return &http.Response{
		StatusCode: http.StatusOK,
		Body: io.NopCloser(strings.NewReader(`{"status":"ok"}`)),
		Header: make(http.Header),
		Request: request,
	}, nil
}

func TestClientRetriesConnectionError(t *testing.T) {
	t.Parallel()

	transport := &retryTransport{}
	client, err := NewClient("http://127.0.0.1:8765")
	if err != nil {
		t.Fatalf("NewClient returned error: %v", err)
	}
	client.HTTPClient = &http.Client{Transport: transport}

	var result JSONObject
	err = client.DoJSON(
		context.Background(),
		http.MethodGet,
		"/v1/health",
		nil,
		&result,
	)
	if err != nil {
		t.Fatalf("DoJSON returned error: %v", err)
	}
	if result["status"] != "ok" {
		t.Errorf("status = %v, want ok", result["status"])
	}
	if transport.attempts.Load() != 2 {
		t.Errorf("attempts = %d, want 2", transport.attempts.Load())
	}
}

func TestClientRejectsHTTPError(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(
		writer http.ResponseWriter,
		request *http.Request,
	) {
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = io.WriteString(writer, `{"detail":"invalid request"}`)
	}))
	defer server.Close()

	client, err := NewClient(server.URL)
	if err != nil {
		t.Fatalf("NewClient returned error: %v", err)
	}

	err = client.DoJSON(
		context.Background(),
		http.MethodGet,
		"/v1/example",
		nil,
		&JSONObject{},
	)
	if err == nil {
		t.Fatal("DoJSON returned nil error")
	}
	if !strings.Contains(err.Error(), "HTTP 422") {
		t.Errorf("error = %v, want HTTP 422", err)
	}
	if !strings.Contains(err.Error(), "invalid request") {
		t.Errorf("error = %v, want response detail", err)
	}
}

func TestNewClientValidatesURL(t *testing.T) {
	t.Parallel()

	for _, baseURL := range []string{
		"://invalid",
		"ftp://example.test",
		"/relative/path",
	} {
		_, err := NewClient(baseURL)
		if err == nil {
			t.Errorf("NewClient(%q) returned nil error", baseURL)
		}
	}
}

func TestRememberFixtureAndGetFixture(t *testing.T) {
	t.Setenv("AGENT_QA_CORE_URL", "")
	var requests atomic.Int32

	server := httptest.NewServer(http.HandlerFunc(func(
		writer http.ResponseWriter,
		request *http.Request,
	) {
		requests.Add(1)
		writer.Header().Set("Content-Type", "application/json")
		if request.Method == http.MethodPost && request.URL.Path == "/v1/fixtures" {
			_, _ = io.WriteString(writer, `{"id":1,"name":"order"}`)
			return
		}
		if request.Method == http.MethodGet &&
			strings.HasPrefix(request.URL.Path, "/v1/fixtures/") {
			_, _ = io.WriteString(writer, `{"id":1,"name":"order","type":"json"}`)
			return
		}
		http.NotFound(writer, request)
	}))
	defer server.Close()
	t.Setenv("AGENT_QA_CORE_URL", server.URL)

	created, err := RememberFixture(
		context.Background(),
		"order",
		"json",
		JSONObject{"state": "pending"},
		[]string{"orders"},
	)
	if err != nil {
		t.Fatalf("RememberFixture returned error: %v", err)
	}
	if created["name"] != "order" {
		t.Errorf("created name = %v, want order", created["name"])
	}

	retrieved, err := GetFixture(context.Background(), "order", nil)
	if err != nil {
		t.Fatalf("GetFixture returned error: %v", err)
	}
	if retrieved["type"] != "json" {
		t.Errorf("retrieved type = %v, want json", retrieved["type"])
	}
	if requests.Load() != 2 {
		t.Errorf("request count = %d, want 2", requests.Load())
	}
}

func TestRememberFailureAndRecordTestRun(t *testing.T) {
	t.Setenv("AGENT_QA_CORE_URL", "")

	server := httptest.NewServer(http.HandlerFunc(func(
		writer http.ResponseWriter,
		request *http.Request,
	) {
		writer.Header().Set("Content-Type", "application/json")
		if request.URL.Path == "/v1/failures" {
			_, _ = io.WriteString(writer, `{"id":2,"resolved":false}`)
			return
		}
		if request.URL.Path == "/v1/test-runs" {
			_, _ = io.WriteString(writer, `{"id":3,"status":"failed"}`)
			return
		}
		http.NotFound(writer, request)
	}))
	defer server.Close()
	t.Setenv("AGENT_QA_CORE_URL", server.URL)

	failure, err := RememberFailure(
		context.Background(),
		"TestOrder",
		"expected 201",
		"stack trace",
		"service unavailable",
		"wait for readiness",
		nil,
	)
	if err != nil {
		t.Fatalf("RememberFailure returned error: %v", err)
	}
	if failure["id"] != float64(2) {
		t.Errorf("failure id = %v, want 2", failure["id"])
	}

	run, err := RecordTestRun(
		context.Background(),
		"TestOrder",
		"go",
		"failed",
		0.5,
		"expected 201",
	)
	if err != nil {
		t.Fatalf("RecordTestRun returned error: %v", err)
	}
	if run["status"] != "failed" {
		t.Errorf("run status = %v, want failed", run["status"])
	}
}

func TestRememberAndGetContract(t *testing.T) {
	t.Setenv("AGENT_QA_CORE_URL", "")

	server := httptest.NewServer(http.HandlerFunc(func(
		writer http.ResponseWriter,
		request *http.Request,
	) {
		writer.Header().Set("Content-Type", "application/json")
		if request.Method == http.MethodPost &&
			request.URL.Path == "/v1/contracts" {
			_, _ = io.WriteString(writer, `{"id":4,"method":"GET"}`)
			return
		}
		if request.Method == http.MethodGet &&
			strings.Contains(request.URL.Path, "/v1/contracts/") {
			_, _ = io.WriteString(writer, `{"id":4,"status_code":200}`)
			return
		}
		http.NotFound(writer, request)
	}))
	defer server.Close()
	t.Setenv("AGENT_QA_CORE_URL", server.URL)

	created, err := RememberContract(
		context.Background(),
		"/orders",
		"GET",
		JSONObject{"type": "object"},
		JSONObject{"type": "object"},
		200,
		nil,
	)
	if err != nil {
		t.Fatalf("RememberContract returned error: %v", err)
	}
	if created["method"] != "GET" {
		t.Errorf("method = %v, want GET", created["method"])
	}

	statusCode := 200
	retrieved, err := GetContract(
		context.Background(),
		"/orders",
		"GET",
		&statusCode,
	)
	if err != nil {
		t.Fatalf("GetContract returned error: %v", err)
	}
	if retrieved["status_code"] != float64(200) {
		t.Errorf("status_code = %v, want 200", retrieved["status_code"])
	}
}

func TestClientHonorsCanceledContext(t *testing.T) {
	t.Parallel()

	client, err := NewClient("http://127.0.0.1:1")
	if err != nil {
		t.Fatalf("NewClient returned error: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	err = client.DoJSON(ctx, http.MethodGet, "/v1/health", nil, &JSONObject{})
	if err == nil {
		t.Fatal("DoJSON returned nil error")
	}
	if !errors.Is(err, context.Canceled) {
		t.Logf("request error was %v", err)
	}
}

func TestConnectionErrorSatisfiesNetworkError(t *testing.T) {
	t.Parallel()

	var networkError net.Error = temporaryConnectionError{}
	if !networkError.Timeout() {
		t.Fatal("temporary connection error should report timeout")
	}
	if !networkError.Temporary() {
		t.Fatal("temporary connection error should report temporary status")
	}
}