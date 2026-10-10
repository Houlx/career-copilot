import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import type { Job } from './api';

let jobs: Job[];
let fail: boolean;
let confirmed: boolean;
let finish: (() => void) | undefined;
let delay: boolean;
const job: Job = {
  id: 'synthetic-job',
  title: 'Synthetic Python role',
  raw_jd: 'Synthetic JD',
  status: 'completed',
  coverage: 50,
  profile_version: 3,
  outdated: true,
  requirements: [
    {
      id: 'req-001',
      text: 'Python and C++',
      importance: 'required',
      category: 'skill',
      scoreability: 'SCORED',
      match: {
        status: 'PARTIAL',
        reason: 'Python evidence, C++ missing.',
        evidence_ids: ['e1'],
        evidence: [
          {
            id: 'e1',
            type: 'project',
            title: 'Python project',
            normalized_content: 'Built synthetic Python tool.',
          },
        ],
      },
    },
    {
      id: 'req-002',
      text: 'SQL',
      importance: 'preferred',
      category: 'skill',
      scoreability: 'SCORED',
      match: { status: 'GAP', reason: 'No SQL evidence', evidence_ids: [], evidence: [] },
    },
    {
      id: 'req-003',
      text: '身体健康',
      importance: 'required',
      category: 'personal_trait',
      scoreability: 'NON_SCORABLE',
      match: null,
    },
    {
      id: 'req-004',
      text: '出差',
      importance: 'required',
      category: 'logistics',
      scoreability: 'NEEDS_CONFIRMATION',
      match: null,
    },
    {
      id: 'req-005',
      text: 'Python',
      importance: 'required',
      category: 'skill',
      scoreability: 'SCORED',
      match: { status: 'MATCH', reason: 'Supported', evidence_ids: ['e1'], evidence: [] },
    },
  ],
};

beforeEach(() => {
  window.history.replaceState({}, '', '/jobs');
  jobs = [];
  fail = false;
  confirmed = true;
  delay = false;
  finish = undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      let data: unknown;
      if (url === '/api/settings')
        data = { ai_mode: 'fake', debug_enabled: true, models: [], prompts: {} };
      else if (url === '/api/profile')
        data = {
          profile: {
            confirmed,
            evidence: [],
            profile_version: 3,
            updated_at: '2026-10-10T00:00:00Z',
          },
        };
      else if (url === '/api/jobs/analyze') {
        if (delay)
          await new Promise<void>((resolve) => {
            finish = resolve;
          });
        if (fail)
          return { ok: false, json: async () => ({ error_code: 'MATCH_VALIDATION_FAILED' }) };
        jobs = [structuredClone(job)];
        data = { job: jobs[0] };
      } else if (url === '/api/jobs') data = { jobs: structuredClone(jobs) };
      else if (options?.method === 'DELETE') {
        jobs = [];
        data = { deleted: true };
      } else data = { job: structuredClone(jobs[0]) };
      return { ok: true, json: async () => data };
    }),
  );
});

async function paste() {
  const input = await screen.findByLabelText('粘贴 JD');
  fireEvent.change(input, { target: { value: 'Synthetic JD' } });
  await waitFor(() => expect(screen.getByRole('button', { name: '分析岗位' })).toBeEnabled());
}

it('only analyzes after explicit click, renders report and evidence and persists on reload', async () => {
  const view = render(<App />);
  await paste();
  expect(vi.mocked(fetch).mock.calls.every(([, options]) => !options)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '分析岗位' }));
  await screen.findByRole('heading', { name: 'Synthetic Python role' });
  expect(screen.getByText('50.0%')).toBeInTheDocument();
  expect(screen.getByText(/Profile version used: 3/)).toBeInTheDocument();
  expect(screen.getByText(/已过期 · Outdated/)).toBeInTheDocument();
  for (const text of [
    'MATCH',
    'PARTIAL',
    'GAP',
    '不计入岗位要求覆盖度',
    '需要用户确认 · 不计入岗位要求覆盖度',
  ])
    expect(screen.getByText(text)).toBeInTheDocument();
  fireEvent.click(screen.getByText('Evidence：Python project'));
  expect(screen.getByText('Built synthetic Python tool.')).toBeInTheDocument();
  expect(screen.getByText('project · Python project')).toBeInTheDocument();
  view.unmount();
  render(<App />);
  await screen.findByText('50.0%');
  fireEvent.click(screen.getByRole('button', { name: '← 岗位分析历史' }));
  await screen.findByRole('button', { name: 'Synthetic Python role' });
});

it('shows loading, blocks duplicates and permits failure retry with JD retained', async () => {
  render(<App />);
  await paste();
  fail = true;
  delay = true;
  fireEvent.click(screen.getByRole('button', { name: '分析岗位' }));
  await screen.findByText('正在分析岗位…');
  expect(screen.getByRole('button', { name: '分析中…' })).toBeDisabled();
  finish?.();
  expect(await screen.findByRole('alert')).toHaveTextContent('MATCH_VALIDATION_FAILED');
  expect(screen.getByLabelText('粘贴 JD')).toHaveValue('Synthetic JD');
  fail = false;
  delay = false;
  fireEvent.click(screen.getByRole('button', { name: '分析岗位' }));
  await screen.findByRole('heading', { name: 'Synthetic Python role' });
});

it('blocks analysis without confirmed profile', async () => {
  confirmed = false;
  render(<App />);
  await screen.findByText('请先编辑并确认职业资料。');
  fireEvent.change(screen.getByLabelText('粘贴 JD'), { target: { value: 'Synthetic JD' } });
  expect(screen.getByRole('button', { name: '分析岗位' })).toBeDisabled();
});

it('displays null coverage and supports deletion', async () => {
  jobs = [{ ...structuredClone(job), coverage: null }];
  window.history.replaceState({}, '', '/jobs/synthetic-job');
  render(<App />);
  await screen.findByText('可自动评分的岗位要求不足');
  fireEvent.click(screen.getByRole('button', { name: '删除此分析' }));
  await screen.findByText('暂无岗位分析');
  expect(jobs).toHaveLength(0);
});

it('shows recent analyses on dashboard', async () => {
  jobs = [structuredClone(job)];
  window.history.replaceState({}, '', '/');
  render(<App />);
  await screen.findByRole('button', { name: 'Synthetic Python role' });
  expect(screen.getByText('50.0%')).toBeInTheDocument();
  expect(screen.getByText('已过期 · Outdated')).toBeInTheDocument();
});
