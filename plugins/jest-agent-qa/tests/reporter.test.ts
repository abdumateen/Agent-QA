import nock from "nock";

import type {
  AggregatedResult,
  AssertionResult,
  Test,
  TestResult,
} from "@jest/test-result";

import AgentQAReporter from "../src/reporter";

const BASE_URL = "http://127.0.0.1:8765";

function makeAssertion(
  overrides: Partial<AssertionResult> = {},
): AssertionResult {
  return {
    ancestorTitles: ["orders"],
    duration: 125,
    failureDetails: [],
    failureMessages: [],
    fullName: "orders creates an order",
    invocations: 1,
    location: null,
    numPassingAsserts: 1,
    retryReasons: [],
    status: "passed",
    title: "creates an order",
    ...overrides,
  };
}

function makeTestResult(
  assertions: AssertionResult[],
  testExecError?: Error,
): TestResult {
  return {
    testFilePath: "/workspace/orders.test.ts",
    testResults: assertions,
    testExecError,
  } as unknown as TestResult;
}

function makeTest(): Test {
  return {
    path: "/workspace/orders.test.ts",
  } as Test;
}

function makeAggregatedResult(): AggregatedResult {
  return {} as AggregatedResult;
}

afterEach(() => {
  nock.cleanAll();
});

afterAll(() => {
  nock.restore();
});

test("records a passing assertion", async () => {
  const scope = nock(BASE_URL)
    .post("/v1/test-runs", {
      test_name: "/workspace/orders.test.ts orders creates an order",
      framework: "jest",
      status: "passed",
      duration: 0.125,
      error_message: null,
    })
    .reply(201, {
      id: 1,
      status: "passed",
    });

  const reporter = new AgentQAReporter(
    {},
    {
      baseUrl: BASE_URL,
    },
  );

  await reporter.onTestResult(
    makeTest(),
    makeTestResult([makeAssertion()]),
    makeAggregatedResult(),
  );

  expect(scope.isDone()).toBe(true);
});

test("records a skipped assertion", async () => {
  const scope = nock(BASE_URL)
    .post("/v1/test-runs", {
      test_name: "/workspace/orders.test.ts orders skips an order",
      framework: "jest",
      status: "skipped",
      duration: 0,
      error_message: null,
    })
    .reply(201, {
      id: 2,
      status: "skipped",
    });

  const reporter = new AgentQAReporter(
    {},
    {
      baseUrl: BASE_URL,
    },
  );

  await reporter.onTestResult(
    makeTest(),
    makeTestResult([
      makeAssertion({
        duration: null,
        title: "skips an order",
        fullName: "orders skips an order",
        status: "pending",
        numPassingAsserts: 0,
      }),
    ]),
    makeAggregatedResult(),
  );

  expect(scope.isDone()).toBe(true);
});

test("records a failed assertion and its failure memory", async () => {
  const failureText = "Expected status 201, received 503";
  const testName = "/workspace/orders.test.ts orders rejects an unavailable order";

  const runScope = nock(BASE_URL)
    .post("/v1/test-runs", {
      test_name: testName,
      framework: "jest",
      status: "failed",
      duration: 0.25,
      error_message: failureText,
    })
    .reply(201, {
      id: 3,
      status: "failed",
    });

  const failureScope = nock(BASE_URL)
    .post("/v1/failures", {
      test_name: testName,
      error_message: failureText,
      stack_trace: failureText,
      root_cause: "Unclassified test failure",
      fix: "Fix not recorded",
      tags: null,
    })
    .reply(201, {
      id: 4,
      test_name: testName,
    });

  const reporter = new AgentQAReporter(
    {},
    {
      baseUrl: BASE_URL,
    },
  );

  await reporter.onTestResult(
    makeTest(),
    makeTestResult([
      makeAssertion({
        duration: 250,
        title: "rejects an unavailable order",
        fullName: "orders rejects an unavailable order",
        failureMessages: [failureText],
        numPassingAsserts: 0,
        status: "failed",
      }),
    ]),
    makeAggregatedResult(),
  );

  expect(runScope.isDone()).toBe(true);
  expect(failureScope.isDone()).toBe(true);
});

test("records a suite execution error without assertions", async () => {
  const suiteError = new Error("Unable to load test module");
  suiteError.stack = "Error: Unable to load test module";
  const testName = "/workspace/orders.test.ts";

  const runScope = nock(BASE_URL)
    .post("/v1/test-runs", {
      test_name: testName,
      framework: "jest",
      status: "error",
      duration: 0,
      error_message: "Unable to load test module",
    })
    .reply(201, {
      id: 5,
      status: "error",
    });

  const failureScope = nock(BASE_URL)
    .post("/v1/failures", {
      test_name: testName,
      error_message: "Unable to load test module",
      stack_trace: "Error: Unable to load test module",
      root_cause: "Test suite execution error",
      fix: "Fix not recorded",
      tags: null,
    })
    .reply(201, {
      id: 6,
      test_name: testName,
    });

  const reporter = new AgentQAReporter(
    {},
    {
      baseUrl: BASE_URL,
    },
  );

  await reporter.onTestResult(
    makeTest(),
    makeTestResult([], suiteError),
    makeAggregatedResult(),
  );

  expect(runScope.isDone()).toBe(true);
  expect(failureScope.isDone()).toBe(true);
});

test("does not make a request when a run completes", () => {
  const reporter = new AgentQAReporter(
    {},
    {
      baseUrl: BASE_URL,
    },
  );

  expect(() =>
    reporter.onRunComplete(new Set<unknown>(), makeAggregatedResult()),
  ).not.toThrow();
});

test("does not reject the Jest run when the service is unavailable", async () => {
  nock(BASE_URL)
    .post("/v1/test-runs")
    .replyWithError("connection refused")
    .post("/v1/test-runs")
    .replyWithError("connection refused");

  const reporter = new AgentQAReporter(
    {},
    {
      baseUrl: BASE_URL,
    },
  );

  await expect(
    reporter.onTestResult(
      makeTest(),
      makeTestResult([makeAssertion()]),
      makeAggregatedResult(),
    ),
  ).resolves.toBeUndefined();
});
