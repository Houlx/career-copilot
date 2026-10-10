import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { JobResumes, ResumeDetail } from './Resumes';
import type { Job, Resume } from './api';

const job: Job = {
  id: 'job',
  title: 'Synthetic role',
  raw_jd: 'JD',
  status: 'completed',
  profile_version: 3,
  coverage: 50,
  outdated: false,
  requirements: [],
};
const item = { text: 'Built Python tools', evidence_ids: ['career-test'] };
const resume: Resume = {
  id: 'resume',
  job_id: 'job',
  language: 'en',
  profile_version: 3,
  status: 'completed',
  created_at: '2026-10-10T00:00:00Z',
  outdated: false,
  structured_resume: {
    language: 'en',
    header: { name: '张三', phone: '123', email: 'a@example.test', location: '上海' },
    summary: [item],
    sections: [{ kind: 'skills', items: [item] }],
  },
  markdown: '# Built Python tools\n',
  evidence: [
    {
      id: 'career-test',
      type: 'work',
      title: 'Synthetic evidence',
      normalized_content: 'Used Python at Synthetic Studio',
    },
  ],
};
let fail: boolean;
let finish: (() => void) | undefined;
let delay: boolean;
let list: Resume[];
beforeEach(() => {
  fail = false;
  delay = false;
  finish = undefined;
  list = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        if (delay)
          await new Promise<void>((resolve) => {
            finish = resolve;
          });
        if (fail)
          return { ok: false, json: async () => ({ error_code: 'RESUME_GROUNDING_FAILED' }) };
        return { ok: true, json: async () => ({ resume_id: resume.id }) };
      }
      if (options?.method === 'DELETE') {
        list = [];
        return { ok: true, json: async () => ({ deleted: true }) };
      }
      return {
        ok: true,
        json: async () =>
          url.startsWith('/api/jobs/')
            ? { resumes: structuredClone(list) }
            : { resume: structuredClone(resume) },
      };
    }),
  );
});

it('generates only on click with explicitly selected language, loading and duplicate prevention', async () => {
  const go = vi.fn();
  delay = true;
  render(<JobResumes job={job} go={go} />);
  await screen.findByText('暂无生成的简历');
  expect(vi.mocked(fetch).mock.calls.every(([, opts]) => !opts)).toBe(true);
  fireEvent.change(screen.getByLabelText('简历语言'), { target: { value: 'ja' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate Resume' }));
  await screen.findByRole('status');
  expect(screen.getByRole('button', { name: '处理中…' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '处理中…' }));
  expect(vi.mocked(fetch).mock.calls.filter(([, opts]) => opts?.method === 'POST')).toHaveLength(1);
  expect(
    JSON.parse(
      vi.mocked(fetch).mock.calls.find(([, opts]) => opts?.method === 'POST')?.[1]?.body as string,
    ),
  ).toEqual({ language: 'ja' });
  finish?.();
  await waitFor(() => expect(go).toHaveBeenCalledWith('/resumes/resume'));
});

it('shows failure and allows explicit retry', async () => {
  fail = true;
  render(<JobResumes job={job} go={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Generate Resume' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('RESUME_GROUNDING_FAILED');
  expect(screen.getByRole('button', { name: 'Generate Resume' })).toBeEnabled();
});

it('blocks outdated jobs, lists history and deletes just the resume', async () => {
  list = [{ ...resume, outdated: true }];
  render(<JobResumes job={{ ...job, outdated: true }} go={vi.fn()} />);
  await screen.findByText(/English · .*Outdated/);
  expect(screen.getByRole('button', { name: 'Generate Resume' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '删除简历' }));
  await screen.findByText('暂无生成的简历');
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url, opts]) => url === '/api/resumes/resume' && opts?.method === 'DELETE'),
  ).toBe(true);
});

it('renders detail, evidence, saved Markdown, linked job and print', async () => {
  const go = vi.fn();
  const print = vi.spyOn(window, 'print').mockImplementation(() => {});
  render(<ResumeDetail id="resume" go={go} />);
  await screen.findByRole('heading', { name: '张三' });
  expect(screen.getByText(/Profile version: 3/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('Show Evidence'));
  expect(screen.getAllByText('Used Python at Synthetic Studio')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Markdown View' }));
  expect(screen.getByText('# Built Python tools')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Print / Save as PDF' }));
  expect(print).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: '← 查看关联岗位分析' }));
  expect(go).toHaveBeenCalledWith('/jobs/job');
});

it('keeps outdated resume readable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ resume: { ...resume, outdated: true } }),
    })),
  );
  render(<ResumeDetail id="resume" go={vi.fn()} />);
  await screen.findByText(/This resume was generated from an older Career Profile/);
  expect(screen.getByRole('heading', { name: '张三' })).toBeInTheDocument();
});

it('uses deterministic header, server labels and section order in the document', async () => {
  const artifact = structuredClone(resume);
  artifact.section_labels = {
    summary: 'Professional Summary',
    skills: 'Skills',
    experience: 'Work Experience',
    projects: 'Projects',
  };
  artifact.section_order = ['skills', 'experience', 'projects'];
  artifact.structured_resume.sections = [
    { kind: 'projects', items: [] },
    { kind: 'experience', items: [item] },
    { kind: 'skills', items: [item] },
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({ resume: artifact }) })),
  );
  const { container } = render(<ResumeDetail id="resume" go={vi.fn()} />);
  await screen.findByRole('heading', { name: '张三' });
  const document = container.querySelector('.resume-document')!;
  expect(document.querySelectorAll('h1')).toHaveLength(1);
  expect(document.textContent).toContain('123 | a@example.test | 上海');
  expect(Array.from(document.querySelectorAll('h2')).map((h) => h.textContent)).toEqual([
    'Professional Summary',
    'Skills',
    'Work Experience',
  ]);
});

it('reads historical headline artifacts without treating headline as the page title', async () => {
  const artifact = structuredClone(resume);
  delete artifact.structured_resume.header;
  artifact.structured_resume.headline = { ...item, text: 'Long legacy career headline' };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({ resume: artifact }) })),
  );
  render(<ResumeDetail id="resume" go={vi.fn()} />);
  await screen.findByRole('heading', { name: 'Resume' });
  expect(
    screen.queryByRole('heading', { name: 'Long legacy career headline' }),
  ).not.toBeInTheDocument();
});
