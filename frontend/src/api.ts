export interface Settings {
  ai_mode: 'fake' | 'replay' | 'live';
  app_env: string;
  debug_enabled: boolean;
  debug_payloads: boolean;
  models: {
    role: string;
    provider: string;
    model: string;
    api_key_configured: boolean;
    structured_output_mode?: 'json_schema' | 'json_object' | null;
  }[];
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
export async function api<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method,
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

export interface ProfileEntry {
  source_index: number;
  type: 'education' | 'work' | 'project' | 'skill' | 'other';
  title: string;
  content: string;
}
export interface CareerProfile {
  raw_input: string;
  ai_normalized_profile: { entries: ProfileEntry[] } | null;
  user_edited_profile: { entries: ProfileEntry[] } | null;
  effective_profile: { entries: ProfileEntry[] } | null;
  confirmed: boolean;
  profile_version: number;
  updated_at: string;
  state: 'raw_saved' | 'draft_ready' | 'edited' | 'confirmed';
  evidence: { id: string; title: string; type: string }[];
}
export interface ProfileResponse {
  profile: CareerProfile | null;
  run_id?: string;
}

export interface JobEvidence {
  id: string;
  type: string;
  title: string;
  normalized_content: string;
}
export interface JobRequirement {
  id: string;
  text: string;
  importance: 'required' | 'preferred';
  category: string;
  scoreability: 'SCORED' | 'NEEDS_CONFIRMATION' | 'NON_SCORABLE';
  match: {
    status: 'MATCH' | 'PARTIAL' | 'GAP';
    reason: string;
    evidence_ids: string[];
    evidence: JobEvidence[];
  } | null;
}
export interface Job {
  id: string;
  title: string;
  raw_jd: string;
  status: string;
  coverage: number | null;
  profile_version: number;
  outdated: boolean;
  requirements: JobRequirement[];
}
