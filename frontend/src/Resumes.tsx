import { useEffect, useRef, useState } from 'react';
import { api, type Job, type Resume, type ResumeLanguage } from './api';

const languages = { zh: '中文', en: 'English', ja: '日本語' };
const labels: Record<ResumeLanguage, Record<string, string>> = {
  zh: {
    summary: '个人简介',
    skills: '技能',
    experience: '工作经历',
    projects: '项目经历',
    education: '教育经历',
    additional: '其他',
  },
  en: {
    summary: 'Summary',
    skills: 'Skills',
    experience: 'Experience',
    projects: 'Projects',
    education: 'Education',
    additional: 'Additional',
  },
  ja: {
    summary: '概要',
    skills: 'スキル',
    experience: '職務経歴',
    projects: 'プロジェクト',
    education: '学歴',
    additional: 'その他',
  },
};

export function JobResumes({ job, go }: { job: Job; go: (path: string) => void }) {
  const [language, setLanguage] = useState<ResumeLanguage>('zh');
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void api<{ resumes: Resume[] }>(`/jobs/${encodeURIComponent(job.id)}/resumes`)
      .then((data) => {
        if (active) setResumes(data.resumes ?? []);
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [job.id]);
  async function generate() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await api<{ resume_id: string }>(
        `/jobs/${encodeURIComponent(job.id)}/resumes`,
        { language },
      );
      go(`/resumes/${result.resume_id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function remove(id: string) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      await api(`/resumes/${encodeURIComponent(id)}`, {}, 'DELETE');
      setResumes((rs) => rs.filter((r) => r.id !== id));
    } catch (e) {
      setError(String(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="card">
      <h2>Generated Resumes</h2>
      <label htmlFor="resume-language">简历语言</label>
      <select
        id="resume-language"
        value={language}
        disabled={busy}
        onChange={(e) => setLanguage(e.target.value as ResumeLanguage)}
      >
        {Object.entries(languages).map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
      <button
        className="primary"
        disabled={busy || job.outdated || job.status !== 'completed'}
        onClick={() => void generate()}
      >
        {busy ? '处理中…' : 'Generate Resume'}
      </button>
      {busy && <p role="status">正在处理简历…</p>}
      {job.outdated && <p>请确认当前职业资料并重新分析岗位后生成简历。</p>}
      {error && <p role="alert">{error}</p>}
      {resumes.length === 0 ? (
        <p>暂无生成的简历</p>
      ) : (
        resumes.map((r) => (
          <article key={r.id} className="requirement">
            <span>
              {languages[r.language]} ·{' '}
              {new Date(r.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} ·{' '}
              {r.outdated ? 'Outdated' : 'Current'}
            </span>
            <button disabled={busy} onClick={() => go(`/resumes/${r.id}`)}>
              打开简历
            </button>
            <button disabled={busy} onClick={() => void remove(r.id)}>
              删除简历
            </button>
          </article>
        ))
      )}
    </section>
  );
}

export function ResumeDetail({ id, go }: { id: string; go: (path: string) => void }) {
  const [resume, setResume] = useState<Resume>();
  const [error, setError] = useState('');
  const [markdown, setMarkdown] = useState(false);
  useEffect(() => {
    let active = true;
    setResume(undefined);
    setError('');
    void api<{ resume: Resume }>(`/resumes/${encodeURIComponent(id)}`)
      .then((data) => {
        if (active) setResume(data.resume);
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [id]);
  if (!resume) return <p role={error ? 'alert' : 'status'}>{error || '加载简历…'}</p>;
  const draft = resume.structured_resume;
  const names = labels[resume.language];
  const facts = [draft.headline, ...draft.summary, ...draft.sections.flatMap((s) => s.items)];
  return (
    <>
      <div className="resume-controls">
        <button onClick={() => go(`/jobs/${resume.job_id}`)}>← 查看关联岗位分析</button>
        <p>
          {languages[resume.language]} · Profile version: {resume.profile_version} ·{' '}
          {resume.outdated ? 'Outdated' : 'Current'}
        </p>
        {resume.outdated && (
          <p role="status" className="error">
            This resume was generated from an older Career Profile. 历史简历已过期。
          </p>
        )}
        <button onClick={() => setMarkdown((v) => !v)}>Markdown View</button>
        <button onClick={() => window.print()}>Print / Save as PDF</button>
      </div>
      {markdown && <pre className="resume-controls">{resume.markdown}</pre>}
      <article className="resume-document" lang={resume.language}>
        <h1>{draft.headline.text}</h1>
        <section>
          <h2>{names.summary}</h2>
          {draft.summary.map((i, n) => (
            <p key={n}>{i.text}</p>
          ))}
        </section>
        {draft.sections.map((s) => (
          <section key={s.kind}>
            <h2>{names[s.kind]}</h2>
            <ul>
              {s.items.map((i, n) => (
                <li key={n}>{i.text}</li>
              ))}
            </ul>
          </section>
        ))}
      </article>
      <details className="card resume-controls">
        <summary>Show Evidence</summary>
        {facts.map((item, n) => (
          <details key={n}>
            <summary>{item.text}</summary>
            {item.evidence_ids.map((eid) => {
              const e = resume.evidence.find((e) => e.id === eid);
              return (
                <div key={eid}>
                  <code>{eid}</code>
                  <p>{e?.title}</p>
                  <p>{e?.normalized_content}</p>
                </div>
              );
            })}
          </details>
        ))}
      </details>
    </>
  );
}
