import type {
  AggregatedResult,
  AssertionResult,
  Test,
  TestResult,
} from "@jest/test-result";

import {
  AgentQAClient,
  AgentQAClientError,
  AgentQAClientOptions,
} from "./client";

export interface AgentQAReporterOptions extends AgentQAClientOptions {}

export default class AgentQAReporter {
  private readonly client: AgentQAClient;

  public constructor(
    _globalConfig: unknown,
    options: AgentQAReporterOptions = {},
  ) {
    this.client = new AgentQAClient(options);
  }

  public async onTestResult(
    test: Test,
    testResult: TestResult,
    _aggregatedResult: AggregatedResult,
  ): Promise<void> {
    const assertions = testResult.testResults;
    if (assertions.length === 0) {
      if (testResult.testExecError !== undefined) {
        await this.reportSuiteError(test.path, testResult);
      }
      return;
    }

    await Promise.all(
      assertions.map((assertion) =>
        this.reportAssertion(test.path, assertion),
      ),
    );
  }

  public onRunComplete(
    _contexts: unknown,
    _results: AggregatedResult,
  ): void {
    return;
  }

  private async reportAssertion(
    testPath: string,
    assertion: AssertionResult,
  ): Promise<void> {
    const testName = buildTestName(testPath, assertion);
    const status = mapStatus(assertion.status);
    const errorMessage = failureMessage(assertion);

    try {
      await this.client.recordTestRun({
        testName,
        framework: "jest",
        status,
        duration: durationInSeconds(assertion.duration),
        errorMessage,
      });

      if (status === "failed") {
        await this.client.rememberFailure({
          testName,
          errorMessage: errorMessage ?? "Test failed",
          stackTrace: assertion.failureMessages.join("\n\n") ||
            "No stack trace was provided.",
          rootCause: "Unclassified test failure",
          fix: "Fix not recorded",
        });
      }
    } catch (error: unknown) {
      reportClientError("assertion", testName, error);
    }
  }

  private async reportSuiteError(
    testPath: string,
    testResult: TestResult,
  ): Promise<void> {
    const testName = testPath;
    const message = testResult.testExecError?.message ?? "Test suite failed";
    const stackTrace =
      testResult.testExecError?.stack ?? "No stack trace was provided.";

    try {
      await this.client.recordTestRun({
        testName,
        framework: "jest",
        status: "error",
        duration: 0,
        errorMessage: message,
      });
      await this.client.rememberFailure({
        testName,
        errorMessage: message,
        stackTrace,
        rootCause: "Test suite execution error",
        fix: "Fix not recorded",
      });
    } catch (error: unknown) {
      reportClientError("suite", testName, error);
    }
  }
}

function buildTestName(
  testPath: string,
  assertion: AssertionResult,
): string {
  const parts = [...assertion.ancestorTitles, assertion.title].filter(
    (part) => part.trim().length > 0,
  );
  return parts.length === 0 ? testPath : `${testPath} ${parts.join(" ")}`;
}

function mapStatus(
  status: AssertionResult["status"],
): "passed" | "failed" | "skipped" {
  if (status === "failed") {
    return "failed";
  }
  if (status === "pending" || status === "todo" || status === "disabled") {
    return "skipped";
  }
  return "passed";
}

function durationInSeconds(duration: number | null | undefined): number {
  if (
    duration === undefined ||
    duration === null ||
    !Number.isFinite(duration) ||
    duration < 0
  ) {
    return 0;
  }
  return duration / 1000;
}

function failureMessage(assertion: AssertionResult): string | undefined {
  if (assertion.failureMessages.length === 0) {
    return undefined;
  }
  return assertion.failureMessages.join("\n\n");
}

function reportClientError(
  operation: string,
  testName: string,
  error: unknown,
): void {
  const message =
    error instanceof AgentQAClientError
      ? error.message
      : error instanceof Error
        ? error.message
        : "Unknown client error";
  process.stderr.write(
    `Agent QA ${operation} reporting failed for ${testName}: ${message}\n`,
  );
}
