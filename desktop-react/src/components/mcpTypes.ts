export type McpServer = {
  name: string;
  transport: string;
  command: string;
  args: string[];
  cwd: string;
  url: string;
  env_from: Record<string, string>;
  bearer_token_env: string;
  enabled: boolean;
  timeout_seconds: number;
  editable?: boolean;
  connected?: boolean;
  tool_count?: number;
  error?: string;
};

export type McpSetupContext = {
  required_auth?: boolean;
  runtime?: string;
  runtime_available?: boolean;
  registry_name?: string;
  version?: string;
  stale?: boolean;
};
