import { useEffect, useRef, useState } from 'react';
import { api, type Resume, type ResumeDraft } from './api';

interface Proposal {
  id: string;
  base_resume_id: string;
  status: string;
  proposal: {
    assistant_message: string;
    updated_resume: ResumeDraft;
    change_summary: { section_kind: string; summary: string }[];
  };
  diff: { section_kind: string; before: unknown; after: unknown }[];
}
interface Thread {
  id: string;
  base_resume_id: string;
  current_resume_id: string;
  messages: { id: string; role: string; content: string }[];
  proposals: Proposal[];
  history: { resume_id: string; revision_number: number }[];
}
function text(value: unknown): string {
  if (Array.isArray(value)) return value.map(text).join('\n');
  if (value && typeof value === 'object') {
    const obj = value as { text?: string; kind?: string; items?: unknown };
    if (obj.text) return obj.text;
    if (obj.items) return `${obj.kind}\n${text(obj.items)}`;
  }
  return JSON.stringify(value);
}
export function RevisionPanel({ resume, go }: { resume: Resume; go: (path: string) => void }) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [instruction, setInstruction] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  const scope = useRef(resume.id);
  scope.current = resume.id;
  useEffect(() => {
    let active = true;
    setLoaded(false);
    setThread(null);
    setError('');
    setInstruction('');
    void api<{ thread: Thread | null }>(`/resumes/${encodeURIComponent(resume.id)}/revision-thread`)
      .then((r) => {
        if (active) {
          setThread(r.thread);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [resume.id]);
  const expected = {
    expected_resume_id: resume.id,
    expected_profile_version: resume.profile_version,
  };
  const historical = !!thread && thread.current_resume_id !== resume.id;
  const pending = thread?.proposals.find((p) => p.status === 'PENDING');
  async function run(action: 'send' | 'accept' | 'reject') {
    if (locked.current) return;
    const requestScope = resume.id;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      let current = thread;
      if (!current) {
        current = (
          await api<{ thread: Thread }>(
            `/resumes/${encodeURIComponent(resume.id)}/revision-threads`,
            expected,
          )
        ).thread;
      }
      if (action === 'send') {
        const r = await api<{ thread: Thread }>(`/revision-threads/${current.id}/messages`, {
          ...expected,
          content: instruction,
        });
        if (scope.current === requestScope) {
          setThread(r.thread);
          setInstruction('');
        }
      } else if (pending) {
        const r = await api<{ thread: Thread; resume_id?: string }>(
          `/revision-proposals/${pending.id}/${action}`,
          expected,
        );
        if (scope.current === requestScope) {
          if (action === 'accept' && r.resume_id) go(`/resumes/${r.resume_id}`);
          else setThread(r.thread);
        }
      }
    } catch (e) {
      if (scope.current === requestScope) setError(String(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="card resume-controls">
      <h2>Revise with AI</h2>
      {thread && (
        <nav aria-label="Revision history">
          <button onClick={() => go(`/resumes/${thread.base_resume_id}`)}>Base resume</button>
          {thread.history.map((r) => (
            <button key={r.resume_id} onClick={() => go(`/resumes/${r.resume_id}`)}>
              Revision #{r.revision_number}
            </button>
          ))}
        </nav>
      )}
      {resume.parent_resume_id && (
        <button onClick={() => go(`/resumes/${resume.parent_resume_id}`)}>Parent resume</button>
      )}
      {thread?.messages.map((m) => (
        <p key={m.id}>
          <strong>{m.role}: </strong>
          {m.content}
        </p>
      ))}
      {(resume.outdated || historical) && (
        <p role="status">
          历史版本只读。请打开当前已接受的简历；资料或岗位过期时需先重新确认和分析。
        </p>
      )}
      <label htmlFor="revision-instruction">修改要求</label>
      <textarea
        id="revision-instruction"
        value={instruction}
        maxLength={6000}
        disabled={busy || resume.outdated || historical || !loaded}
        onChange={(e) => setInstruction(e.target.value)}
      />
      <button
        disabled={busy || resume.outdated || historical || !loaded || !instruction.trim()}
        onClick={() => void run('send')}
      >
        Send
      </button>
      {busy && <p role="status">正在处理修改…</p>}
      {error && <p role="alert">{error}</p>}
      {pending && !historical && (
        <article aria-label="Pending revision">
          <h3>Pending revision preview</h3>
          {pending.proposal.change_summary.map((c, n) => (
            <p key={n}>{c.summary}</p>
          ))}
          {pending.diff.map((d) => (
            <section key={d.section_kind}>
              <h4>{d.section_kind}</h4>
              <div className="revision-diff">
                <div>
                  <h4>Before</h4>
                  <pre>{text(d.before)}</pre>
                </div>
                <div>
                  <h4>After</h4>
                  <pre>{text(d.after)}</pre>
                </div>
              </div>
            </section>
          ))}
          <button
            className="primary"
            disabled={busy || resume.outdated}
            onClick={() => void run('accept')}
          >
            Accept revision
          </button>
          <button disabled={busy} onClick={() => void run('reject')}>
            Reject
          </button>
        </article>
      )}
    </section>
  );
}
