package agentqa

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const (
	defaultBaseURL    = "http://127.0.0.1:8765"
	requestTimeout    = 5 * time.Second
	maxResponseBytes = 4 << 20
)

type JSONValue = any
type JSONObject = map[string]JSONValue

// Client sends requests to the Agent QA core service.
type Client struct {
	BaseURL    string
	HTTPClient *http.Client
}

// NewClient creates a client for the supplied service URL.
func NewClient(baseURL string) (*Client, error) {
	if strings.TrimSpace(baseURL) == "" {
		baseURL = os.Getenv("AGENT_QA_CORE_URL")
	}
	if strings.TrimSpace(baseURL) == "" {
		baseURL = defaultBaseURL
	}

	parsed, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return nil, fmt.Errorf("parse service URL: %w", err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, fmt.Errorf("service URL must use http or https")
	}
	if parsed.Host == "" {
		return nil, fmt.Errorf("service URL must include a host")
	}

	return &Client{
		BaseURL: parsed.String(),
		HTTPClient: &http.Client{
			Timeout: requestTimeout,
		},
	}, nil
}

// DoJSON sends one JSON request and decodes a successful JSON response.
func (c *Client) DoJSON(
	ctx context.Context,
	method string,
	path string,
	requestBody JSONValue,
	responseBody JSONValue,
) error {
	if c == nil {
		return errors.New("client is nil")
	}
	if c.HTTPClient == nil {
		return errors.New("HTTP client is nil")
	}
	if ctx == nil {
		return errors.New("context is nil")
	}
	if !strings.HasPrefix(path, "/") {
		return errors.New("request path must begin with /")
	}

	var body []byte
	var err error
	if requestBody != nil {
		body, err = json.Marshal(requestBody)
		if err != nil {
			return fmt.Errorf("encode request body: %w", err)
		}
	}

	for attempt := 0; attempt < 2; attempt++ {
		err = c.doRequest(ctx, method, path, body, responseBody)
		if err == nil {
			return nil
		}
		if attempt == 0 && isConnectionError(err) {
			continue
		}
		return err
	}

	return errors.New("request retry loop did not complete")
}

func (c *Client) doRequest(
	parent context.Context,
	method string,
	path string,
	body []byte,
	responseBody JSONValue,
) error {
	ctx, cancel := context.WithTimeout(parent, requestTimeout)
	defer cancel()

	requestURL := c.BaseURL + path
	request, err := http.NewRequestWithContext(
		ctx,
		method,
		requestURL,
		bytes.NewReader(body),
	)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	request.Header.Set("Accept", "application/json")
	if len(body) > 0 {
		request.Header.Set("Content-Type", "application/json")
	}

	response, err := c.HTTPClient.Do(request)
	if err != nil {
		return fmt.Errorf("send request: %w", err)
	}
	defer response.Body.Close()

	responseBytes, err := io.ReadAll(
		io.LimitReader(response.Body, maxResponseBytes+1),
	)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}
	if len(responseBytes) > maxResponseBytes {
		return errors.New("response exceeds the maximum allowed size")
	}

	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf(
			"core service returned HTTP %d: %s",
			response.StatusCode,
			responseDetail(responseBytes),
		)
	}
	if responseBody == nil || len(responseBytes) == 0 {
		return nil
	}
	if err := json.Unmarshal(responseBytes, responseBody); err != nil {
		return fmt.Errorf("decode response body: %w", err)
	}
	return nil
}

func isConnectionError(err error) bool {
	var urlError *url.Error
	if errors.As(err, &urlError) {
		err = urlError.Err
	}

	var networkError net.Error
	if errors.As(err, &networkError) {
		return true
	}

	return errors.Is(err, context.DeadlineExceeded)
}

func responseDetail(body []byte) string {
	var payload struct {
		Detail JSONValue `json:"detail"`
	}
	if err := json.Unmarshal(body, &payload); err == nil && payload.Detail != nil {
		return fmt.Sprint(payload.Detail)
	}
	message := strings.TrimSpace(string(body))
	if message == "" {
		return "no error detail"
	}
	return message
}