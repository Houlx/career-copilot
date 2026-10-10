import { useEffect, useState } from 'react';
import {
  api,
  type BasicInfo,
  type CareerProfile,
  type ProfileEntry,
  type ProfileResponse,
} from './api';

type PageState = 'empty' | CareerProfile['state'] | 'normalizing' | 'failed';
const typeNames = {
  education: '教育经历',
  work: '工作经历',
  project: '项目经历',
  skill: '技能',
  other: '其他',
};

export function ProfileSummary() {
  const [profile, setProfile] = useState<CareerProfile | null>();
  const [error, setError] = useState('');
  useEffect(() => {
    void api<ProfileResponse>('/profile')
      .then((r) => setProfile(r.profile))
      .catch((e) => setError(String(e)));
  }, []);
  return (
    <section className="card">
      <h2>Career Profile</h2>
      {error ? (
        <p role="alert">{error}</p>
      ) : profile === undefined ? (
        <p>加载职业资料…</p>
      ) : (
        <>
          <p>{profile?.confirmed ? 'Confirmed' : profile ? 'Draft' : 'Not started'}</p>
          <p>
            Evidence count: {profile?.evidence.length ?? 0} · Profile version:{' '}
            {profile?.profile_version ?? 0}
          </p>
          {profile && (
            <p>
              Last updated:{' '}
              {new Date(profile.updated_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}
            </p>
          )}
        </>
      )}
    </section>
  );
}

export function ProfilePage({
  go,
  fakeMode,
  debugEnabled,
}: {
  go: (path: string) => void;
  fakeMode: boolean;
  debugEnabled: boolean;
}) {
  const [profile, setProfile] = useState<CareerProfile | null>(null);
  const [basicInfo, setBasicInfo] = useState<BasicInfo>({});
  const [raw, setRaw] = useState('');
  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [state, setState] = useState<PageState>('empty');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [runId, setRunId] = useState('');
  function apply(next: CareerProfile | null) {
    setProfile(next);
    setBasicInfo(next?.basic_info ?? {});
    setRaw(next?.raw_input ?? '');
    setEntries(next?.effective_profile?.entries ?? []);
    setState(next?.state ?? 'empty');
  }
  useEffect(() => {
    let active = true;
    void api<ProfileResponse>('/profile')
      .then((r) => {
        if (active) apply(r.profile);
      })
      .catch((e) => {
        if (active) {
          setError(String(e));
          setState('failed');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  const rawDirty = raw !== (profile?.raw_input ?? '');
  const edited =
    JSON.stringify(entries) !== JSON.stringify(profile?.effective_profile?.entries ?? []) ||
    JSON.stringify(basicInfo) !== JSON.stringify(profile?.basic_info ?? {});
  const busy = saving || state === 'normalizing' || loading;
  async function act(action: 'raw' | 'normalize' | 'edit' | 'confirm') {
    setError('');
    setSaving(true);
    if (action === 'normalize') {
      setState('normalizing');
      setRunId('');
    }
    try {
      const expected_version = profile?.profile_version ?? 0;
      const body =
        action === 'raw'
          ? { expected_version, raw_input: raw }
          : action === 'edit'
            ? { expected_version, profile: { entries }, basic_info: basicInfo }
            : { expected_version };
      const response = await api<ProfileResponse>(
        action === 'edit' ? '/profile' : `/profile/${action}`,
        body,
        action === 'edit' ? 'PUT' : 'POST',
      );
      apply(response.profile);
      if (response.run_id) setRunId(response.run_id);
    } catch (e) {
      setError(String(e));
      setState('failed');
    } finally {
      setSaving(false);
    }
  }
  function change(index: number, patch: Partial<ProfileEntry>) {
    setEntries((old) => old.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)));
  }
  return (
    <>
      <div className="eyebrow">CAREER PROFILE</div>
      <h1>职业资料</h1>
      <p className="lead">保存原始经历，AI 整理后查看、编辑，并明确确认。</p>
      {fakeMode && (
        <p className="hero-note">fake 模式：离线保留输入为一条“其他”事实，用于验证流程。</p>
      )}
      <p role="status">状态：{edited && !busy && state !== 'failed' ? 'edited · 未保存' : state}</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <section className="card">
        <h2>原始职业资料</h2>
        <label htmlFor="raw-profile">粘贴自我介绍、教育、工作、项目及技能信息</label>
        <textarea
          id="raw-profile"
          rows={10}
          maxLength={20000}
          value={raw}
          disabled={busy}
          onChange={(e) => setRaw(e.target.value)}
        />
        <div className="profile-actions">
          <button disabled={busy || !raw.trim() || !rawDirty} onClick={() => void act('raw')}>
            保存原始资料
          </button>
          <button
            className="primary"
            disabled={busy || !profile || rawDirty || edited}
            onClick={() => void act('normalize')}
          >
            {state === 'normalizing' ? 'AI 整理中…' : 'AI 整理'}
          </button>
        </div>
        {rawDirty && <p>原始资料尚未保存。</p>}
      </section>
      {profile?.effective_profile && (
        <section className="card">
          <h2>整理后的职业资料</h2>
          {profile.state === 'raw_saved' && <p>以下为上次整理结果；新原始资料需要重新 AI 整理。</p>}
          <fieldset disabled={busy || rawDirty || profile.state === 'raw_saved'}>
            <h3>基本信息</h3>
            {(['name', 'phone', 'email', 'location'] as const).map((field) => (
              <label key={field}>
                {{ name: '姓名', phone: '电话', email: '邮箱', location: '所在地' }[field]}
                <input
                  value={basicInfo[field] ?? ''}
                  maxLength={300}
                  onChange={(e) =>
                    setBasicInfo((old) => ({
                      ...old,
                      [field]: e.target.value.trim() ? e.target.value : null,
                    }))
                  }
                />
              </label>
            ))}
            {entries.map((entry, index) => (
              <div className="profile-entry" key={entry.source_index}>
                <label>
                  类型
                  <select
                    value={entry.type}
                    onChange={(e) =>
                      change(index, { type: e.target.value as ProfileEntry['type'] })
                    }
                  >
                    {Object.entries(typeNames).map(([value, name]) => (
                      <option key={value} value={value}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  标题
                  <input
                    value={entry.title}
                    maxLength={300}
                    onChange={(e) => change(index, { title: e.target.value })}
                  />
                </label>
                <label>
                  内容
                  <textarea
                    rows={5}
                    maxLength={20000}
                    value={entry.content}
                    onChange={(e) => change(index, { content: e.target.value })}
                  />
                </label>
              </div>
            ))}
            <div className="profile-actions">
              <button
                disabled={!edited || entries.some((e) => !e.title.trim() || !e.content.trim())}
                onClick={() => void act('edit')}
              >
                保存编辑
              </button>
              <button
                className="primary"
                disabled={edited || profile.confirmed}
                onClick={() => void act('confirm')}
              >
                确认职业资料
              </button>
            </div>
          </fieldset>
          {profile.confirmed && !edited && (
            <div className="result">
              <strong>Profile Confirmed</strong>
              <p>Evidence 数量：{profile.evidence.length}</p>
            </div>
          )}
          {profile.user_edited_profile && (
            <details>
              <summary>查看 AI 原始整理版本</summary>
              <pre>{JSON.stringify(profile.ai_normalized_profile, null, 2)}</pre>
            </details>
          )}
        </section>
      )}
      {profile && <p>Profile version: {profile.profile_version}</p>}
      {runId && debugEnabled && (
        <button onClick={() => go(`/debug/runs/${runId}`)}>查看 Profile Trace →</button>
      )}
      {!!profile?.evidence.length && (
        <section className="card">
          <h2>Career Evidence</h2>
          {!profile.confirmed && <p>上次确认的证据仍保留；当前资料需要重新确认。</p>}
          {profile.evidence.map((e) => (
            <p key={e.id}>
              {e.title}
              <br />
              <code>{e.id}</code>
            </p>
          ))}
        </section>
      )}
    </>
  );
}
