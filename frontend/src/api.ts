export interface Settings {
  ai_mode: 'fake' | 'replay' | 'live';
  app_env: string;
  debug_enabled: boolean;
  debug_payloads: boolean;
  models: { role: string; provider: string; model: string; api_key_configured: boolean }[];
  prompts: Record<string, string>;
}
export interface Run {
  run_id: string;
  workflow_name: string;
  workflow_version: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  status: string;
  error_code: string | null;
  total_tokens?: number;
  models?: string[];
  node_count?: number;
}
export interface NodeTrace {
  node_name: string;
  sequence: number;
  started_at: string;
  duration_ms: number;
  status: string;
  input_summary: unknown;
  output_summary: unknown;
  state_changes: unknown;
  validation_result: unknown;
  error_code: string | null;
  ai: Record<string, unknown> | null;
  debug_payload?: unknown;
}
export interface RunDetail extends Run {
  nodes: NodeTrace[];
}
export interface DummyResponse {
  run_id: string;
  state: { message: string; counter: number; result: string };
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error_code ?? error.detail ?? `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}
