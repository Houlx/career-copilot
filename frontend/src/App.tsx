import { resumeReplay } from './resumeReplay';
import { ResumeDetail } from './Resumes';
import { useEffect, useState } from 'react';
import { JobDetail, JobHistory, JobsPage } from './Jobs';
import { ProfilePage, ProfileSummary } from './Profile';
import { api, type DummyResponse, type Run, type RunDetail, type Settings } from './api';

const navigation = [
  ['/', '概览', '◈'],
  ['/profile', '职业资料', '○'],
  ['/jobs', '岗位分析', '▤'],

  ['/settings', '设置', '⚙'],
];

function Json({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

function Dashboard({ go, debugEnabled }: { go: (path: string) => void; debugEnabled: boolean }) {
  const [message, setMessage] = useState('Career Copilot');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DummyResponse>();
  const [error, setError] = useState('');
  async function run() {
    setBusy(true);
    setError('');
    setResult(undefined);
    try {
      setResult(await api<DummyResponse>('/workflows/dummy', { message }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="eyebrow">YOUR NEXT CHAPTER</div>
      <h1>让每一步，都有依据。</h1>
      <p className="lead">从真实职业经历出发，理解岗位要求，准备属于你的下一步。</p>
      <div className="hero-note">
        <span className="dot" /> Phase 4 · Resume Generation 已就绪
      </div>
      <ProfileSummary />
      <JobHistory go={go} compact />
      <div className="steps">
        {[
          ['01', '整理职业资料', '提取事实，编辑并确认', 'Phase 2'],
          ['02', '理解目标岗位', '匹配证据，计算岗位要求覆盖度', 'Phase 3'],
          ['03', '准备针对性简历', '基于事实生成，导出与打印', 'Phase 4'],
        ].map(([n, title, text, phase]) => (
          <article className="card step" key={n}>
            <span className="step-number">{n}</span>
            <h2>{title}</h2>
            <p>{text}</p>
            <span className="tag">{phase} · 已开放</span>
          </article>
        ))}
      </div>
      <section className="card diagnostic">
        <div>
          <div className="eyebrow">FOUNDATION CHECK</div>
          <h2>运行工作流诊断</h2>
          <p>验证 HTTP → LangGraph → 状态变更 → SQLite Trace 的完整链路。</p>
          <p className="muted">两个确定性节点，0 次模型调用。</p>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
        >
          <label htmlFor="message">诊断文本（请勿输入个人信息）</label>
          <input
            id="message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            maxLength={500}
            required
          />
          <button className="primary" disabled={busy || !message.trim()}>
            {busy ? '运行中…' : '运行诊断 →'}
          </button>
        </form>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {result && (
          <div className="result" role="status">
            <strong>✓ 工作流完成</strong>
            <p>
              {result.state.result} · counter = {result.state.counter}
            </p>
            <code>{result.run_id}</code>
            {debugEnabled && (
              <button onClick={() => go(`/debug/runs/${result.run_id}`)}>查看节点追踪 →</button>
            )}
          </div>
        )}
      </section>
      <p className="disclaimer">
        岗位要求覆盖度将衡量已确认职业资料对岗位要求的覆盖程度，不代表获得面试或录用的概率。
      </p>
    </>
  );
}

function SettingsPage({ settings }: { settings: Settings }) {
  return (
    <>
      <div className="eyebrow">CONFIGURATION</div>
      <h1>设置</h1>
      <p className="lead">当前服务配置。修改根目录 .env 后重启后端生效。</p>
      <section className="card">
        <h2>
          运行模式 <span className="tag">{settings.ai_mode}</span>
        </h2>
        <p>fake：确定性输出 · replay：读取已保存输出 · live：真实 Provider 请求</p>
        <dl>
          <dt>应用环境</dt>
          <dd>{settings.app_env}</dd>
          <dt>完整调试内容</dt>
          <dd>{settings.debug_payloads ? '已开启' : '关闭（默认）'}</dd>
        </dl>
        <p className="muted">默认 fake。只有显式配置 AI_MODE=live 才允许真实模型请求。</p>
      </section>
      <div className="model-grid">
        {settings.models.map((model) => (
          <section className="card" key={model.role}>
            <div className="eyebrow">{model.role.toUpperCase()} MODEL</div>
            <h2>{model.model}</h2>
            <dl>
              <dt>Provider</dt>
              <dd>{model.provider}</dd>
              {model.structured_output_mode && (
                <>
                  <dt>结构化输出</dt>
                  <dd>{model.structured_output_mode}</dd>
                </>
              )}
              <dt>API Key</dt>
              <dd>{model.api_key_configured ? '已配置 · 不显示密钥' : '未配置'}</dd>
            </dl>
          </section>
        ))}
      </div>
      <section className="card">
        <h2>Prompt 版本</h2>
        <Json value={settings.prompts} />
      </section>
    </>
  );
}

function DebugRuns({ path, go }: { path: string; go: (path: string) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [detail, setDetail] = useState<RunDetail>();
  const [selected, setSelected] = useState<number>(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [replay, setReplay] = useState<unknown>();
  const [replaying, setReplaying] = useState(false);
  const [replayTask, setReplayTask] = useState('diagnostic');
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setDetail(undefined);
    setSelected(0);
    const id = path.slice('/debug/runs/'.length);
    const request =
      path === '/debug/runs'
        ? api<{ runs: Run[] }>('/debug/runs').then((data) => {
            if (active) setRuns(data.runs);
          })
        : api<RunDetail>(`/debug/runs/${encodeURIComponent(id)}`).then((data) => {
            if (active) setDetail(data);
          });
    void request
      .catch((e) => {
        if (active) setError(String(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  async function replayDiagnostic() {
    setReplaying(true);
    setError('');
    setReplay(undefined);
    try {
      const fact = {
        type: 'skill',
        title: 'Python',
        content: '使用 Python。',
        source_quote: '使用 Python。',
      };
      const payload =
        replayTask === 'resume-generation-v1'
          ? resumeReplay
          : replayTask === 'jd-extract-v1' || replayTask === 'jd-requirements-v2'
            ? { raw_jd: 'Synthetic Python developer. Python required.' }
            : replayTask === 'scoreability-v1'
              ? { requirements: [{ text: 'Python', importance: 'required', category: 'skill' }] }
              : replayTask === 'matching-v1'
                ? {
                    requirements: [
                      {
                        requirement_index: 0,
                        text: 'Python',
                        importance: 'required',
                        category: 'skill',
                        scoreability: 'SCORED',
                      },
                    ],
                    evidence: [
                      {
                        id: 'synthetic-python',
                        type: 'skill',
                        title: 'Python',
                        normalized_content: 'Python',
                      },
                    ],
                  }
                : replayTask === 'profile-facts-v1'
                  ? { raw_input: '使用 Python。' }
                  : replayTask === 'profile-normalize-v1'
                    ? { facts: [fact] }
                    : {};
      setReplay(await api('/debug/replay', { task: replayTask, payload }));
    } catch (e) {
      setError(String(e));
    } finally {
      setReplaying(false);
    }
  }
  const node = detail?.nodes[selected];
  return (
    <>
      <div className="eyebrow">DEVELOPER WORKSPACE</div>
      <div className="title-row">
        <h1>Debug Runs</h1>
        <button onClick={() => setRevision((r) => r + 1)}>刷新</button>
      </div>
      <p className="lead">定位到每一个节点。默认只保存摘要，不记录个人资料全文。</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">加载运行记录…</p>
      ) : detail ? (
        <>
          <button onClick={() => go('/debug/runs')}>← 所有运行</button>
          <section className="card">
            <h2>
              {detail.workflow_name} <span className="tag">{detail.status}</span>
            </h2>
            <code>{detail.run_id}</code>
            <p>
              {detail.workflow_version} · {detail.duration_ms?.toFixed(1)} ms
            </p>
            {detail.error_code && <p className="error">{detail.error_code}</p>}
          </section>
          <div className="trace-layout">
            <section className="card">
              <h2>Node Timeline</h2>
              {detail.nodes.map((n, index) => (
                <button
                  className={`node ${selected === index ? 'selected' : ''}`}
                  key={n.sequence}
                  onClick={() => setSelected(index)}
                >
                  <span>
                    {n.status === 'completed' ? '✓' : '×'} {n.node_name}
                  </span>
                  <small>{n.duration_ms.toFixed(2)} ms</small>
                </button>
              ))}
            </section>
            {node && (
              <section className="card node-detail">
                <h2>{node.node_name}</h2>
                <h3>Metadata</h3>
                <Json
                  value={{
                    sequence: node.sequence,
                    started_at: node.started_at,
                    status: node.status,
                    duration_ms: node.duration_ms,
                    error_code: node.error_code,
                  }}
                />
                <h3>State Diff</h3>
                <Json value={node.state_changes} />
                <h3>Input / Output Summary</h3>
                <Json value={{ input: node.input_summary, output: node.output_summary }} />
                <h3>Validation</h3>
                <Json value={node.validation_result} />
                <h3>Model / Prompt / Tokens</h3>
                {node.ai ? (
                  <Json value={node.ai} />
                ) : (
                  <p>确定性 Python 节点 · 未调用模型 · 0 tokens</p>
                )}
                {node.debug_payload !== undefined && (
                  <>
                    <h3>Debug Payload（显式开启）</h3>
                    <Json value={node.debug_payload} />
                  </>
                )}
              </section>
            )}
          </div>
        </>
      ) : (
        <section className="card table-wrap">
          {runs.length === 0 ? (
            <p>还没有运行记录。前往概览运行一次诊断。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Run / Workflow</th>
                  <th>状态</th>
                  <th>耗时</th>
                  <th>Tokens / Model</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => go(`/debug/runs/${run.run_id}`)}
                      >
                        {run.run_id.slice(0, 8)} →
                      </button>
                      <small>
                        {run.workflow_name} ·{' '}
                        {new Date(run.started_at).toLocaleString('zh-CN', {
                          timeZone: 'Asia/Shanghai',
                        })}
                      </small>
                    </td>
                    <td>
                      <span className="tag">{run.status}</span>
                    </td>
                    <td>{run.duration_ms?.toFixed(1) ?? '—'} ms</td>
                    <td>
                      {run.total_tokens ?? 0}
                      <small>{run.models?.join(', ') || 'Python only'}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
      <section className="card">
        <h2>独立 AI Node Replay</h2>
        <p>
          读取所选任务的 synthetic 固定输出并进行 Pydantic 校验。不会发起模型请求或修改用户数据。
        </p>
        <label>
          Replay task
          <select value={replayTask} onChange={(e) => setReplayTask(e.target.value)}>
            <option value="diagnostic">diagnostic</option>
            <option value="jd-requirements-v2">jd-requirements-v2</option>
            <option value="scoreability-v1">scoreability-v1</option>
            <option value="jd-extract-v1">jd-extract-v1</option>
            <option value="matching-v1">matching-v1</option>
            <option value="resume-generation-v1">resume-generation-v1</option>
            <option value="profile-facts-v1">profile-facts-v1</option>
            <option value="profile-normalize-v1">profile-normalize-v1</option>
          </select>
        </label>
        <button disabled={replaying} onClick={() => void replayDiagnostic()}>
          {replaying ? '读取中…' : `Replay ${replayTask}`}
        </button>
        {replay !== undefined && <Json value={replay} />}
      </section>
    </>
  );
}

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  const [settings, setSettings] = useState<Settings>();
  const [error, setError] = useState('');
  useEffect(() => {
    const pop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', pop);
    void api<Settings>('/settings')
      .then(setSettings)
      .catch((e) => setError(String(e)));
    return () => window.removeEventListener('popstate', pop);
  }, []);
  function go(next: string) {
    window.history.pushState({}, '', next);
    setPath(next);
  }
  const links = settings?.debug_enabled
    ? [...navigation, ['/debug/runs', 'Debug Runs', '⌘']]
    : navigation;
  const placeholder = navigation.find(([href]) => href === path)?.[1];
  return (
    <div className="app">
      <aside>
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            go('/');
          }}
        >
          <span className="brand-icon">C</span>
          <span>
            Career<span className="brand-sub">COPILOT</span>
          </span>
        </a>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">
          {links.map(([href, label, icon]) => (
            <a
              href={href}
              key={href}
              className={(href === '/' ? path === href : path.startsWith(href)) ? 'active' : ''}
              onClick={(e) => {
                e.preventDefault();
                go(href);
              }}
            >
              <span aria-hidden="true">{icon}</span>
              {label}
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="dot" /> 本地个人工作空间<small>Phase 4 · v0.4.0</small>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <span>个人职业助手</span>
          <span className="mode">{settings?.ai_mode ?? '连接中'} mode</span>
        </header>
        <main>
          {error ? (
            <div role="alert" className="card error">
              无法连接后端：{error}
              <p>请启动 FastAPI 服务后刷新页面。</p>
            </div>
          ) : !settings ? (
            <p role="status">加载配置…</p>
          ) : path === '/' ? (
            <Dashboard go={go} debugEnabled={settings.debug_enabled} />
          ) : path === '/profile' ? (
            <ProfilePage
              go={go}
              fakeMode={settings.ai_mode === 'fake'}
              debugEnabled={settings.debug_enabled}
            />
          ) : path === '/jobs' ? (
            <JobsPage go={go} fakeMode={settings.ai_mode === 'fake'} />
          ) : path.startsWith('/jobs/') ? (
            <JobDetail id={path.slice(6)} go={go} debugEnabled={settings.debug_enabled} />
          ) : path.startsWith('/resumes/') ? (
            <ResumeDetail key={path} id={path.slice(9)} go={go} />
          ) : path === '/settings' ? (
            <SettingsPage settings={settings} />
          ) : path.startsWith('/debug/runs') && settings.debug_enabled ? (
            <DebugRuns path={path} go={go} />
          ) : placeholder ? (
            <>
              <div className="eyebrow">COMING NEXT</div>
              <h1>{placeholder}</h1>
              <section className="card">
                <h2>将在后续阶段开放</h2>
                <p>职业资料和岗位分析已开放。简历生成将在后续阶段实现。</p>
                <button onClick={() => go('/')}>返回概览</button>
              </section>
            </>
          ) : (
            <>
              <h1>页面不存在</h1>
              <button onClick={() => go('/')}>返回概览</button>
            </>
          )}
        </main>
        <footer>Career Copilot · 真实经历，有据可循。</footer>
      </div>
    </div>
  );
}
