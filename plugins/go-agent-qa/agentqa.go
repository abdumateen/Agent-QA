package agentqa

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path"
	"strings"
	"testing"
	"time"
)

// RecordTestRun records one test execution in the core service.
func RecordTestRun(
	ctx context.Context,
	testName string,
	framework string,
	status string,
	duration float64,
	errorMessage string,
) (JSONObject, error) {
	client, err := NewClient("")
	if err != nil {
		return nil, err
	}

	payload := JSONObject{
		"test_name":  testName,
		"framework":  framework,
		"status":     status,
		"duration":   duration,
		"error_message": optionalString(errorMessage),
	}
	var result JSONObject
	if err := client.DoJSON(ctx, http.MethodPost, "/v1/test-runs", payload, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// RememberFailure stores a failure and its diagnostic information.
func RememberFailure(
	ctx context.Context,
	testName string,
	errorMessage string,
	stackTrace string,
	rootCause string,
	fix string,
	tags []string,
) (JSONObject, error) {
	client, err := NewClient("")
	if err != nil {
		return nil, err
	}

	payload := JSONObject{
		"test_name":    testName,
		"error_message": errorMessage,
		"stack_trace":  stackTrace,
		"root_cause":   rootCause,
		"fix":          fix,
		"tags":         optionalTags(tags),
	}
	var result JSONObject
	if err := client.DoJSON(ctx, http.MethodPost, "/v1/failures", payload, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// RememberFixture creates or replaces a named fixture.
func RememberFixture(
	ctx context.Context,
	name string,
	fixtureType string,
	content JSONObject,
	tags []string,
) (JSONObject, error) {
	client, err := NewClient("")
	if err != nil {
		return nil, err
	}

	payload := JSONObject{
		"name":    name,
		"type":    fixtureType,
		"content": content,
		"tags":    optionalTags(tags),
	}
	var result JSONObject
	if err := client.DoJSON(ctx, http.MethodPost, "/v1/fixtures", payload, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// GetFixture retrieves a fixture by name and optional type.
func GetFixture(
	ctx context.Context,
	name string,
	fixtureType *string,
) (JSONObject, error) {
	client, err := NewClient("")
	if err != nil {
		return nil, err
	}

	requestPath := "/v1/fixtures/" + url.PathEscape(name)
	if fixtureType != nil {
		requestPath += "?type=" + url.QueryEscape(*fixtureType)
	}
	var result JSONObject
	if err := client.DoJSON(ctx, http.MethodGet, requestPath, nil, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// RememberContract creates or replaces an API contract.
func RememberContract(
	ctx context.Context,
	endpoint string,
	method string,
	requestSchema JSONObject,
	responseSchema JSONObject,
	statusCode int,
	tags []string,
) (JSONObject, error) {
	client, err := NewClient("")
	if err != nil {
		return nil, err
	}

	payload := JSONObject{
		"endpoint":        endpoint,
		"method":          method,
		"request_schema":  requestSchema,
		"response_schema": responseSchema,
		"status_code":     statusCode,
		"tags":            optionalTags(tags),
	}
	var result JSONObject
	if err := client.DoJSON(ctx, http.MethodPost, "/v1/contracts", payload, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// GetContract retrieves an API contract by endpoint, method, and optional status.
func GetContract(
	ctx context.Context,
	endpoint string,
	method string,
	statusCode *int,
) (JSONObject, error) {
	client, err := NewClient("")
	if err != nil {
		return nil, err
	}

	requestPath := path.Join(
		"/v1/contracts",
		url.PathEscape(endpoint),
		url.PathEscape(method),
	)
	if statusCode != nil {
		requestPath += fmt.Sprintf("?status_code=%d", *statusCode)
	}
	var result JSONObject
	if err := client.DoJSON(ctx, http.MethodGet, requestPath, nil, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// NewTestMain runs the Go test suite and records its aggregate result.
func NewTestMain(m *testing.M) {
	started := time.Now()
	exitCode := m.Run()
	status := "passed"
	if exitCode != 0 {
		status = "failed"
	}

	ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
	defer cancel()

	_, err := RecordTestRun(
		ctx,
		"go-test-suite",
		"go",
		status,
		time.Since(started).Seconds(),
		"",
	)
	if err != nil {
		_, _ = fmt.Fprintf(
			os.Stderr,
			"agentqa: unable to record test suite result: %v\n",
			err,
		)
	}
	os.Exit(exitCode)
}

func optionalString(value string) JSONValue {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

func optionalTags(tags []string) JSONValue {
	if len(tags) == 0 {
		return nil
	}
	return tags
}