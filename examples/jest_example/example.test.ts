import {
  rememberContract,
  rememberFixture,
} from "jest-agent-qa";

describe("order memory", () => {
  test("stores an order fixture", async () => {
    const fixture = await rememberFixture({
      name: "jest-example-order",
      type: "json",
      content: {
        order_id: 42,
        state: "pending",
      },
      tags: ["example", "orders"],
    });

    expect(fixture.name).toBe("jest-example-order");
    expect(fixture.content).toEqual({
      order_id: 42,
      state: "pending",
    });
  });

  test("stores an order API contract", async () => {
    const contract = await rememberContract({
      endpoint: "/orders",
      method: "POST",
      requestSchema: {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        type: "object",
        properties: {
          sku: {
            type: "string",
            minLength: 1,
          },
          quantity: {
            type: "integer",
            minimum: 1,
          },
        },
        required: ["sku", "quantity"],
        additionalProperties: false,
      },
      responseSchema: {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        type: "object",
        properties: {
          order_id: {
            type: "integer",
            minimum: 1,
          },
          state: {
            type: "string",
          },
        },
        required: ["order_id", "state"],
        additionalProperties: false,
      },
      statusCode: 201,
      tags: ["example", "orders"],
    });

    expect(contract.endpoint).toBe("/orders");
    expect(contract.method).toBe("POST");
    expect(contract.status_code).toBe(201);
  });
});