import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { RevisionPanel } from './RevisionPanel';
import type { Resume } from './api';

const item = { text: 'Python engineer', evidence_ids: ['synthetic'] };
const resume: Resume = {
  id: 'base',
  job_id: 'job',
  profile_version: 3,
  language: 'en',
  status: 'completed',
  created_at: 'date',
  outdated: false,
  markdown: '# Python',
  evidence: [],
  structured_resume: {
    language: 'en',
    headline: item,
    summary: [item],
    sections: [{ kind: 'skills', items: [item] }],
  },
};
const empty = {
  id: 'thread',
  base_resume_id: 'base',
  current_resume_id: 'base',
  messages: [],
  proposals: [],
  history: [],
};
const pending = {
  ...empty,
  messages: [{ id: 'm', role: 'assistant', content: 'Review changes' }],
  proposals: [
    {
      id: 'proposal',
      base_resume_id: 'base',
      status: 'PENDING',
      proposal: {
        assistant_message: 'Review changes',
        updated_resume: resume.structured_resume,
        change_summary: [{ section_kind: 'headline', summary: 'Emphasize Python' }],
      },
      diff: [
        { section_kind: 'headline', before: item, after: { ...item, text: 'Python developer' } },
      ],
    },
  ],
};
let initial: typeof empty | typeof pending | null;
let fail: boolean;
beforeEach(() => {
  initial = null;
  fail = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (!options) return { ok: true, json: async () => ({ thread: initial }) };
      if (fail) return { ok: false, json: async () => ({ error_code: 'REVISION_BASE_OUTDATED' }) };
      if (url.endsWith('/revision-threads'))
        return { ok: true, json: async () => ({ thread: empty }) };
      if (url.endsWith('/messages')) return { ok: true, json: async () => ({ thread: pending }) };
      if (url.endsWith('/accept')) return { ok: true, json: async () => ({ resume_id: 'child' }) };
      return { ok: true, json: async () => ({ thread: { ...pending, proposals: [] } }) };
    }),
  );
});
it('sends explicit instruction, shows deterministic diff, persists only on Accept', async () => {
  const go = vi.fn();
  render(<RevisionPanel resume={resume} go={go} />);
  await waitFor(() => expect(screen.getByLabelText('修改要求')).toBeEnabled());
  expect(vi.mocked(fetch).mock.calls).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('修改要求'), { target: { value: 'Emphasize Python' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText('Before');
  expect(screen.getByText('After')).toBeInTheDocument();
  expect(screen.getByText('Python developer')).toBeInTheDocument();
  expect(go).not.toHaveBeenCalled();
  expect(
    vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/accept')),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Accept revision' }));
  await waitFor(() => expect(go).toHaveBeenCalledWith('/resumes/child'));
  const body = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).endsWith('/accept'))?.[1]?.body;
  expect(JSON.parse(body as string)).toEqual({
    expected_resume_id: 'base',
    expected_profile_version: 3,
  });
});
it('rejects without navigation or changing accepted resume', async () => {
  initial = pending;
  const go = vi.fn();
  render(<RevisionPanel resume={resume} go={go} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
  await waitFor(() => expect(screen.queryByLabelText('Pending revision')).not.toBeInTheDocument());
  expect(go).not.toHaveBeenCalled();
  expect(screen.getByText('Review changes')).toBeInTheDocument();
});
it('shows conflict and leaves proposal available for review', async () => {
  initial = pending;
  fail = true;
  render(<RevisionPanel resume={resume} go={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Accept revision' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('REVISION_BASE_OUTDATED');
  expect(screen.getByLabelText('Pending revision')).toBeInTheDocument();
});
it('keeps outdated and historical versions read only, with history links', async () => {
  initial = { ...empty, current_resume_id: 'child', history: [] };
  const go = vi.fn();
  render(
    <RevisionPanel resume={{ ...resume, outdated: true, parent_resume_id: 'parent' }} go={go} />,
  );
  await screen.findByText(/历史版本只读/);
  expect(screen.getByLabelText('修改要求')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Parent resume' }));
  expect(go).toHaveBeenCalledWith('/resumes/parent');
});
