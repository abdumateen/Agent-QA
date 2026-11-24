import {
  AgentQAClient,
  AgentQAClientOptions,
  ContractInput,
  FixtureInput,
  JsonObject,
  MemoryRecord,
} from "./client";

export {
  AgentQAClient,
  AgentQAClientError,
} from "./client";

export type {
  AgentQAClientOptions,
  ContractInput,
  FixtureInput,
  JsonObject,
  JsonPrimitive,
  JsonValue,
  MemoryRecord,
  TestRunInput,
  FailureInput,
} from "./client";

export { default as AgentQAReporter } from "./reporter";

const defaultClient = new AgentQAClient();

export async function rememberFixture(
  input: FixtureInput,
): Promise<MemoryRecord> {
  return defaultClient.rememberFixture(input);
}

export async function rememberContract(
  input: ContractInput,
): Promise<MemoryRecord> {
  return defaultClient.rememberContract(input);
}

export function createClient(
  options: AgentQAClientOptions = {},
): AgentQAClient {
  return new AgentQAClient(options);
}

