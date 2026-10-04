package example_test

import (
	"context"
	"testing"

	agentqa "github.com/agent-qa/agent-qa/plugins/go-agent-qa"
)

func TestRememberOrderFixture(t *testing.T) {
	ctx := context.Background()

	fixture, err := agentqa.RememberFixture(
		ctx,
		"go-example-order",
		"json",
		agentqa.JSONObject{
			"order_id": float64(42),
			"state":    "pending",
		},
		[]string{"example", "orders"},
	)
	if err != nil {
		t.Fatalf("remember fixture: %v", err)
	}

	if fixture["name"] != "go-example-order" {
		t.Fatalf("fixture name = %v, want go-example-order", fixture["name"])
	}
}

func TestRememberOrderContract(t *testing.T) {
	ctx := context.Background()

	contract, err := agentqa.RememberContract(
		ctx,
		"/orders",
		"POST",
		agentqa.JSONObject{
			"$schema": "https://json-schema.org/draft/2020-12/schema",
			"type":    "object",
			"required": []any{
				"sku",
				"quantity",
			},
			"properties": agentqa.JSONObject{
				"sku": agentqa.JSONObject{
					"type":      "string",
					"minLength": float64(1),
				},
				"quantity": agentqa.JSONObject{
					"type":    "integer",
					"minimum": float64(1),
				},
			},
		},
		agentqa.JSONObject{
			"$schema": "https://json-schema.org/draft/2020-12/schema",
			"type":    "object",
			"required": []any{
				"order_id",
				"state",
			},
			"properties": agentqa.JSONObject{
				"order_id": agentqa.JSONObject{
					"type":    "integer",
					"minimum": float64(1),
				},
				"state": agentqa.JSONObject{
					"type": "string",
				},
			},
		},
		201,
		[]string{"example", "orders"},
	)
	if err != nil {
		t.Fatalf("remember contract: %v", err)
	}

	if contract["endpoint"] != "/orders" {
		t.Fatalf("endpoint = %v, want /orders", contract["endpoint"])
	}
	if contract["method"] != "POST" {
		t.Fatalf("method = %v, want POST", contract["method"])
	}
}