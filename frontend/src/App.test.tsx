import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { resumeReplay } from './resumeReplay';

const settings = {
  ai_mode: 'fake',
  app_env: 'development',
  debug_enabled: true,
  debug_payloads: false,
  models: [
    {
      role: 'judge',
      provider: 'openai-compatible',
      model: 'Not configured',
      api_key_configured: false,
      timeout_seconds: 30,
    },
  ],
  task_policies: {
    'profile-facts-v1': { reasoning_effort: 'low' },
    'jd-requirements-v2': { reasoning_effort: 'high' },
    'matching-v1': { reasoning_effort: 'high' },
    'resume-generation-v2': { reasoning_effort: 'high' },
    'resume-revision-agent-v1': { reasoning_effort: 'high' },
  },
  prompts: { diagnostic: 'diagnostic-v1' },
};
const detail = {
  run_id: 'run-1',
  workflow_name: 'dummy',
  workflow_version: 'dummy-v1',
  status: 'completed',
  duration_ms: 2,
  nodes: [
    {
      node_name: 'mutate_state',
      sequence: 1,
      status: 'completed',
      started_at: '2026-10-10T00:00:00Z',
      duration_ms: 1,
      input_summary: {},
      output_summary: {},
      state_changes: { counter: { before: 0, after: 1 } },
      validation_result: { status: 'passed' },
      ai: null,
      error_code: null,
    },
  ],
};

beforeEach(() => {
  window.history.replaceState({}, '', '/');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const value =
        url === '/api/settings'
          ? settings
          : url === '/api/workflows/dummy'
            ? { run_id: 'run-1', state: { result: 'Ready: Career Copilot', counter: 1 } }
            : url === '/api/debug/runs/run-1'
              ? detail
              : url === '/api/debug/replay'
                ? { output: { message: 'Provider connection verified' } }
                : url === '/api/profile'
                  ? { profile: null }
                  : { runs: [] };
      return { ok: true, json: async () => value };
    }),
  );
});

describe('Phase 1 shell', () => {
  it('runs a diagnostic explicitly and opens its real node timeline', async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '运行诊断 →' }));
    expect(await screen.findByText('✓ 工作流完成')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看节点追踪 →' }));
    expect(await screen.findByText('Node Timeline')).toBeInTheDocument();
    expect(screen.getByText('State Diff')).toBeInTheDocument();
    expect(screen.getByText(/确定性 Python 节点/)).toBeInTheDocument();
    expect(screen.queryByText('Debug Payload（显式开启）')).not.toBeInTheDocument();
  });

  it('shows configuration and no secret values', async () => {
    window.history.replaceState({}, '', '/settings');
    render(<App />);
    expect(await screen.findByText('Prompt 版本')).toBeInTheDocument();
    expect(screen.getByText('请求超时（秒）')).toBeInTheDocument();
    expect(screen.getByText('30')).toBeInTheDocument();
    expect(screen.getByText('未配置')).toBeInTheDocument();
    expect(screen.getByText('关闭（默认）')).toBeInTheDocument();
  });

  it('replays independently from debug page', async () => {
    window.history.replaceState({}, '', '/debug/runs');
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Replay diagnostic' }));
    expect(await screen.findByText(/Provider connection verified/)).toBeInTheDocument();
    expect(screen.getByText(/还没有运行记录/)).toBeInTheDocument();
  });

  it('submits the resume v2 task with the resume evidence payload', async () => {
    window.history.replaceState({}, '', '/debug/runs');
    render(<App />);
    fireEvent.change(await screen.findByRole('combobox'), {
      target: { value: 'resume-generation-v2' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Replay resume-generation-v2' }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/debug/replay',
        expect.objectContaining({
          body: JSON.stringify({ task: 'resume-generation-v2', payload: resumeReplay }),
        }),
      ),
    );
  });

  it('hides debug navigation outside development', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ ...settings, debug_enabled: false }) })),
    );
    render(<App />);
    await screen.findByText('让每一步，都有依据。');
    expect(screen.queryByRole('link', { name: /Debug Runs/ })).not.toBeInTheDocument();
  });

  it('displays request failures and re-enables diagnostic control', async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);
    const button = await screen.findByRole('button', { name: '运行诊断 →' });
    await screen.findByText('Not started');
    fetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error_code: 'WORKFLOW_FAILED' }),
    } as Response);
    fireEvent.click(button);
    expect(await screen.findByRole('alert')).toHaveTextContent('WORKFLOW_FAILED');
    await waitFor(() => expect(button).toBeEnabled());
  });

  it('does not start a workflow on initial load', async () => {
    render(<App />);
    await screen.findByText('让每一步，都有依据。');
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => options === undefined)).toBe(true);
    expect(fetch).toHaveBeenCalledWith('/api/settings', undefined);
  });
});

describe('Generation reasoning configuration', () => {
  it.each([null, 'low', 'high', 'max'])('shows read-only effort %s', async (effort) => {
    window.history.replaceState({}, '', '/settings');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          ...settings,
          models: [{ ...settings.models[0], role: 'generation', reasoning_effort: effort }],
        }),
      })),
    );
    render(<App />);
    expect(await screen.findByText('Reasoning effort')).toBeInTheDocument();
    expect(
      screen.getByText(effort ? effort[0].toUpperCase() + effort.slice(1) : 'Default'),
    ).toBeInTheDocument();
    expect(screen.getByText('Task inference policies')).toBeInTheDocument();
    expect(screen.getByText('profile-facts-v1')).toBeInTheDocument();
    expect(screen.getByText('low')).toBeInTheDocument();
    expect(screen.getByText('jd-requirements-v2')).toBeInTheDocument();
    expect(screen.getByText('matching-v1')).toBeInTheDocument();
    expect(screen.getByText('resume-generation-v2')).toBeInTheDocument();
    expect(screen.getByText('resume-revision-agent-v1')).toBeInTheDocument();
    expect(screen.getAllByText('high')).toHaveLength(4);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => options === undefined)).toBe(true);
  });
});
