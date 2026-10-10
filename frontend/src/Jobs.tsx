import { useEffect, useState } from 'react';
import { api, type Job, type ProfileResponse } from './api';

export const coverageNote =
  '表示当前已确认职业资料对可自动评分岗位要求的覆盖程度，不代表获得面试或录用的概率。';

function Coverage({ job }: { job: Job }) {
  return (
    <p>
      岗位要求覆盖度：
      <strong>
        {job.coverage === null ? '可自动评分的岗位要求不足' : `${job.coverage.toFixed(1)}%`}
      </strong>
    </p>
  );
}

export function JobHistory({
  go,
  compact = false,
}: {
  go: (path: string) => void;
  compact?: boolean;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void api<{ jobs: Job[] }>('/jobs')
      .then((data) => {
        if (active) setJobs(data.jobs ?? []);
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <section className="card">
      <h2>{compact ? '最近岗位分析' : '岗位分析历史'}</h2>
      {error && <p role="alert">{error}</p>}
      {!jobs.length && <p>暂无岗位分析</p>}
      {(compact ? jobs.slice(0, 5) : jobs).map((job) => (
        <article key={job.id}>
          <button onClick={() => go(`/jobs/${job.id}`)}>{job.title}</button>
          <Coverage job={job} />
          {job.outdated && <span className="tag">已过期 · Outdated</span>}
        </article>
      ))}
    </section>
  );
}

export function JobsPage({ go, fakeMode }: { go: (path: string) => void; fakeMode: boolean }) {
  const [jd, setJd] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void api<ProfileResponse>('/profile')
      .then((data) => setConfirmed(!!data.profile?.confirmed))
      .catch((e) => setError(String(e)));
  }, []);
  async function analyze() {
    setBusy(true);
    setError('');
    try {
      const result = await api<{ job: Job }>('/jobs/analyze', { raw_jd: jd });
      go(`/jobs/${result.job.id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h1>岗位分析</h1>
      <p className="lead">粘贴岗位说明，查看要求与职业证据。</p>
      {fakeMode && (
        <p className="hero-note">
          fake 模式使用固定 synthetic 岗位要求及字面证据匹配，仅用于流程验证。
        </p>
      )}
      {!confirmed && (
        <p>
          请先编辑并确认职业资料。<button onClick={() => go('/profile')}>前往职业资料</button>
        </p>
      )}
      <section className="card">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void analyze();
          }}
        >
          <label htmlFor="jd">粘贴 JD</label>
          <textarea
            id="jd"
            value={jd}
            maxLength={30000}
            disabled={busy}
            onChange={(e) => setJd(e.target.value)}
          />
          <button className="primary" disabled={busy || !confirmed || !jd.trim()}>
            {busy ? '分析中…' : '分析岗位'}
          </button>
        </form>
        {busy && <p role="status">正在分析岗位…</p>}
        {error && (
          <p role="alert" className="error">
            {error} · 可保留 JD 重试
          </p>
        )}
      </section>
      <JobHistory go={go} />
    </>
  );
}

export function JobDetail({
  id,
  go,
  debugEnabled,
}: {
  id: string;
  go: (path: string) => void;
  debugEnabled: boolean;
}) {
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setJob(undefined);
    setError('');
    void api<{ job: Job }>(`/jobs/${encodeURIComponent(id)}`)
      .then((data) => {
        if (active) setJob(data.job);
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [id]);
  async function remove() {
    setBusy(true);
    try {
      await api(`/jobs/${encodeURIComponent(id)}`, {}, 'DELETE');
      go('/jobs');
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button onClick={() => go('/jobs')}>← 岗位分析历史</button>
      {error && <p role="alert">{error}</p>}
      {!job ? (
        <p role="status">加载报告…</p>
      ) : (
        <>
          <h1>{job.title}</h1>
          <section className="card">
            <Coverage job={job} />
            <p>{coverageNote}</p>
            <p>Profile version used: {job.profile_version}</p>
            {job.outdated && (
              <p className="tag">已过期 · Outdated：职业资料已变化，请确认当前资料后重新分析。</p>
            )}
            <button disabled={busy} onClick={() => void remove()}>
              删除此分析
            </button>
            {debugEnabled && <button onClick={() => go('/debug/runs')}>查看运行追踪</button>}
          </section>
          <section className="card">
            <h2>岗位要求</h2>
            {job.requirements.map((r) => (
              <article className="requirement" key={r.id}>
                <h3>{r.text}</h3>
                <p>
                  {r.importance} · {r.category} · {r.scoreability}
                </p>
                {r.scoreability === 'NON_SCORABLE' ? (
                  <p>不计入岗位要求覆盖度</p>
                ) : r.scoreability === 'NEEDS_CONFIRMATION' ? (
                  <p>需要用户确认 · 不计入岗位要求覆盖度</p>
                ) : (
                  r.match && (
                    <>
                      <strong>{r.match.status}</strong>
                      <p>{r.match.reason}</p>
                      {r.match.evidence.map((e) => (
                        <details key={e.id}>
                          <summary>Evidence：{e.title}</summary>
                          <p>
                            {e.type} · {e.title}
                          </p>
                          <p>{e.normalized_content}</p>
                          <code>{e.id}</code>
                        </details>
                      ))}
                    </>
                  )
                )}
              </article>
            ))}
          </section>
          <section className="card">
            <h2>原始 JD</h2>
            <p className="raw-jd">{job.raw_jd}</p>
          </section>
        </>
      )}
    </>
  );
}
