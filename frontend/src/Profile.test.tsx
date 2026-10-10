import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { CareerProfile } from './api';

let profile: CareerProfile | null;
let failAction: string;
const calls: { url: string; method?: string; body?: Record<string, unknown> }[] = [];
const settings = { ai_mode: 'fake', debug_enabled: true, models: [], prompts: {} };
const entry = {
  source_index: 0,
  type: 'skill' as const,
  title: 'Python',
  content: '使用 Python。',
};

beforeEach(() => {
  window.history.replaceState({}, '', '/profile');
  profile = null;
  failAction = '';
  calls.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : undefined;
      calls.push({ url, method: options?.method, body });
      if (url === '/api/settings') return { ok: true, json: async () => settings };
      if (options && url === failAction)
        return { ok: false, json: async () => ({ error_code: 'MODEL_OUTPUT_INVALID' }) };
      if (url === '/api/profile/raw') {
        profile = {
          raw_input: body.raw_input,
          ai_normalized_profile: null,
          user_edited_profile: null,
          effective_profile: null,
          confirmed: false,
          profile_version: 1,
          updated_at: '2026-10-10T00:00:00Z',
          state: 'raw_saved',
          evidence: [],
        };
      } else if (url === '/api/profile/normalize' && profile) {
        profile = {
          ...profile,
          ai_normalized_profile: { entries: [entry] },
          effective_profile: { entries: [entry] },
          profile_version: 2,
          state: 'draft_ready',
        };
      } else if (url === '/api/profile' && options?.method === 'PUT' && profile) {
        profile = {
          ...profile,
          effective_profile: body.profile,
          user_edited_profile: body.profile,
          confirmed: false,
          profile_version: profile.profile_version + 1,
          state: 'edited',
        };
      } else if (url === '/api/profile/confirm' && profile) {
        profile = {
          ...profile,
          confirmed: true,
          profile_version: profile.profile_version + 1,
          state: 'confirmed',
          evidence: [{ id: 'career-skill-stable', title: 'Python', type: 'skill' }],
        };
      }
      return {
        ok: true,
        json: async () => ({
          profile: structuredClone(profile),
          run_id: url.endsWith('/normalize') ? 'profile-run' : undefined,
        }),
      };
    }),
  );
});

async function draft() {
  render(<App />);
  const raw = await screen.findByLabelText(/粘贴自我介绍/);
  await waitFor(() => expect(raw).toBeEnabled());
  fireEvent.change(raw, { target: { value: '使用 Python。' } });
  fireEvent.click(screen.getByRole('button', { name: '保存原始资料' }));
  await screen.findByText('状态：raw_saved');
  fireEvent.click(screen.getByRole('button', { name: 'AI 整理' }));
  await screen.findByText('整理后的职业资料');
}

describe('Career Profile', () => {
  it('saves, normalizes explicitly, edits, confirms and reloads through navigation', async () => {
    await draft();
    expect(profile?.confirmed).toBe(false);
    fireEvent.change(screen.getByLabelText('内容'), {
      target: { value: '使用 Python 编写脚本。' },
    });
    expect(screen.getByRole('button', { name: '确认职业资料' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存编辑' }));
    await screen.findByText('状态：edited');
    expect(profile?.raw_input).toBe('使用 Python。');
    expect(profile?.ai_normalized_profile?.entries[0].content).toBe('使用 Python。');
    expect(calls.filter((c) => c.url.endsWith('/normalize'))).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '确认职业资料' }));
    await screen.findByText('Profile Confirmed');
    expect(screen.getByText('Evidence 数量：1')).toBeInTheDocument();
    expect(screen.getByText('career-skill-stable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: /概览/ }));
    await screen.findByText('Confirmed');
    expect(screen.getByText(/Profile version: 4/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: /职业资料/ }));
    await screen.findByText('Profile Confirmed');
    expect(screen.getByLabelText('内容')).toHaveValue('使用 Python 编写脚本。');
    expect(calls.filter((c) => c.url.endsWith('/confirm'))).toHaveLength(1);
  });

  it('does not normalize or confirm on load', async () => {
    render(<App />);
    await screen.findByText('状态：empty');
    expect(calls.every((c) => !c.method)).toBe(true);
    expect(screen.getByRole('button', { name: 'AI 整理' })).toBeDisabled();
  });

  it('shows failure and permits retry while preserving saved raw', async () => {
    await draft();
    failAction = '/api/profile/normalize';
    fireEvent.click(screen.getByRole('button', { name: 'AI 整理' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('MODEL_OUTPUT_INVALID');
    expect(screen.getByText('状态：failed')).toBeInTheDocument();
    expect(screen.getByLabelText(/粘贴自我介绍/)).toHaveValue('使用 Python。');
    await waitFor(() => expect(screen.getByRole('button', { name: 'AI 整理' })).toBeEnabled());
    failAction = '';
    fireEvent.click(screen.getByRole('button', { name: 'AI 整理' }));
    await screen.findByText('状态：draft_ready');
  });

  it('blocks normalization and confirmation while raw changes are unsaved', async () => {
    await draft();
    fireEvent.change(screen.getByLabelText(/粘贴自我介绍/), { target: { value: 'new raw' } });
    expect(screen.getByRole('button', { name: 'AI 整理' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '确认职业资料' })).toBeDisabled();
  });

  it('shows normalizing explicitly and prevents duplicate requests', async () => {
    await draft();
    const original = vi.mocked(fetch).getMockImplementation()!;
    let finish: ((value: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation((url, options) =>
      String(url).endsWith('/normalize')
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : original(url, options),
    );
    fireEvent.click(screen.getByRole('button', { name: 'AI 整理' }));
    await screen.findByText('状态：normalizing');
    expect(screen.getByRole('button', { name: 'AI 整理中…' })).toBeDisabled();
    finish?.({ ok: true, json: async () => ({ profile }) } as Response);
    await screen.findByText('状态：draft_ready');
  });
});
