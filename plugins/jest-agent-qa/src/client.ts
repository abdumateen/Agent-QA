import axios, {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  AxiosResponse,
} from "axios";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export type MemoryRecord = JsonObject;

export interface AgentQAClientOptions {
  readonly baseUrl?: string;
  readonly timeoutMs?: number;
}

export interface TestRunInput {
  readonly testName: string;
  readonly framework: string;
  readonly status: "passed" | "failed" | "error" | "skipped";
  readonly duration: number;
  readonly errorMessage?: string;
}

export interface FailureInput {
  readonly testName: string;
  readonly errorMessage: string;
  readonly stackTrace: string;
  readonly rootCause: string;
  readonly fix: string;
  readonly tags?: readonly string[];
}

export interface FixtureInput {
  readonly name: string;
  readonly type: string;
  readonly content: JsonObject;
  readonly tags?: readonly string[];
}

export interface ContractInput {
  readonly endpoint: string;
  readonly method: string;
  readonly requestSchema: JsonObject;
  readonly responseSchema: JsonObject;
  readonly statusCode: number;
  readonly tags?: readonly string[];
}

const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
const DEFAULT_TIMEOUT_MS = 5000;
const CONNECTION_ERROR_CODES = new Set([
  "ECONNABORTED",
  "ECONNREFUSED",
  "ECONNRESET",
  "ENETUNREACH",
  "ENOTFOUND",
  "ETIMEDOUT",
]);

export class AgentQAClientError extends Error {
  public readonly statusCode: number | undefined;

  public constructor(
    message: string,
    statusCode?: number,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "AgentQAClientError";
    this.statusCode = statusCode;
  }
}

export class AgentQAClient {
  private readonly http: AxiosInstance;
  private readonly maxAttempts = 2;

  public constructor(options: AgentQAClientOptions = {}) {
    const baseUrl =
      options.baseUrl ??
      process.env.AGENT_QA_CORE_URL ??
      DEFAULT_BASE_URL;
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

    if (timeoutMs <= 0) {
      throw new RangeError("Timeout must be positive.");
    }

    this.http = axios.create({
      baseURL: baseUrl.replace(/\/+$/, ""),
      timeout: timeoutMs,
      proxy: false,
      validateStatus: () => true,
    });
  }

  public async recordTestRun(
    input: TestRunInput,
  ): Promise<MemoryRecord> {
    return this.requestObject("POST", "/v1/test-runs", {
      test_name: input.testName,
      framework: input.framework,
      status: input.status,
      duration: input.duration,
      error_message: input.errorMessage ?? null,
    });
  }

  public async rememberFailure(
    input: FailureInput,
  ): Promise<MemoryRecord> {
    return this.requestObject("POST", "/v1/failures", {
      test_name: input.testName,
      error_message: input.errorMessage,
      stack_trace: input.stackTrace,
      root_cause: input.rootCause,
      fix: input.fix,
      tags: input.tags === undefined ? null : [...input.tags],
    });
  }

  public async rememberFixture(
    input: FixtureInput,
  ): Promise<MemoryRecord> {
    return this.requestObject("POST", "/v1/fixtures", {
      name: input.name,
      type: input.type,
      content: input.content,
      tags: input.tags === undefined ? null : [...input.tags],
    });
  }

  public async getFixture(
    name: string,
    type?: string,
  ): Promise<MemoryRecord> {
    return this.requestObject("GET", `/v1/fixtures/${encodePath(name)}`, {
      ...(type === undefined ? {} : { type }),
    });
  }

  public async rememberContract(
    input: ContractInput,
  ): Promise<MemoryRecord> {
    return this.requestObject("POST", "/v1/contracts", {
      endpoint: input.endpoint,
      method: input.method,
      request_schema: input.requestSchema,
      response_schema: input.responseSchema,
      status_code: input.statusCode,
      tags: input.tags === undefined ? null : [...input.tags],
    });
  }

  public async getContract(
    endpoint: string,
    method: string,
    statusCode?: number,
  ): Promise<MemoryRecord> {
    return this.requestObject(
      "GET",
      `/v1/contracts/${encodePath(endpoint)}/${encodePath(method)}`,
      statusCode === undefined ? {} : { status_code: statusCode },
    );
  }

  private async requestObject(
    method: "GET" | "POST",
    url: string,
    paramsOrData: JsonObject,
  ): Promise<MemoryRecord> {
    const response = await this.request<JsonValue>(
      method,
      url,
      method === "GET" ? { params: paramsOrData } : { data: paramsOrData },
    );
    if (!isJsonObject(response)) {
      throw new AgentQAClientError(
        "The core service returned an unexpected object.",
      );
    }
    return response;
  }

  private async request<T extends JsonValue>(
    method: "GET" | "POST",
    url: string,
    config: AxiosRequestConfig<JsonObject>,
  ): Promise<T> {
    let attempt = 0;

    while (attempt < this.maxAttempts) {
      attempt += 1;
      let response: AxiosResponse<T>;

      try {
        response = await this.http.request<T>({
          ...config,
          method,
          url,
        });
      } catch (error: unknown) {
        if (attempt < this.maxAttempts && isConnectionError(error)) {
          continue;
        }
        throw toClientError(error);
      }

      if (response.status >= 200 && response.status < 300) {
        return response.data;
      }

      throw new AgentQAClientError(
        errorMessage(response.data, response.status),
        response.status,
      );
    }

    throw new AgentQAClientError(
      "The core request could not be completed.",
    );
  }
}

function encodePath(value: string): string {
  if (value.trim().length === 0) {
    throw new AgentQAClientError("Path parameters must not be empty.");
  }
  return encodeURIComponent(value).replace(/\./g, "%2E");
}

function isJsonObject(value: JsonValue): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isConnectionError(error: unknown): boolean {
  if (!axios.isAxiosError(error)) {
    return false;
  }
  const axiosError = error as AxiosError<JsonValue>;
  return (
    axiosError.code !== undefined &&
    CONNECTION_ERROR_CODES.has(axiosError.code)
  );
}

function toClientError(error: unknown): AgentQAClientError {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<JsonValue>;
    return new AgentQAClientError(
      axiosError.message || "Communication with the core service failed.",
      axiosError.response?.status,
      { cause: error },
    );
  }
  if (error instanceof Error) {
    return new AgentQAClientError(error.message, undefined, { cause: error });
  }
  return new AgentQAClientError(
    "Communication with the core service failed.",
  );
}

function errorMessage(data: JsonValue, status: number): string {
  if (isJsonObject(data)) {
    const detail = data.detail;
    if (typeof detail === "string") {
      return `The core service returned HTTP ${status}: ${detail}`;
    }
  }
  return `The core service returned HTTP ${status}.`;
}
