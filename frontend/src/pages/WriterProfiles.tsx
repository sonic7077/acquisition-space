import { useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, Copy, Download, Plus, Sparkles } from 'lucide-react';
import { api, patch, post, saveText, type Row } from '../api';
import { Card, Field, Modal, Notice } from '../components/ui';
import { useRows, verifiedKnowledge, type Ctx } from '../context';

const STATUS: Record<string, string> = {
  draft: '草稿', compiled: '待审核', active: '可用', stale: '需更新', archived: '已归档',
};
const KINDS: Record<string, string> = {
  delete: '删掉', add: '加上', replace: '换词', reorder: '挪动',
};
const KNOWLEDGE_LIMIT = 8;

interface WriterForm {
  name: string;
  industry: string;
  skill: string;
  answers: string[];
  knowledge_ids: string[];
}

function writerForm(writer?: Row): WriterForm {
  return {
    name: writer?.name || '',
    industry: writer?.industry || '',
    skill: writer?.skill || 'viral-rewrite-writer',
    answers: Array.from({ length: 12 }, (_, index) => writer?.answers?.[index] || ''),
    knowledge_ids: writer?.knowledge_ids || [],
  };
}

interface Props {
  c: Ctx;
  ipId: string;
  onRewrite?: (writerId: string) => void;
}

export default function WriterProfiles({ c, ipId, onRewrite }: Props) {
  const writers = useRows('/writers', c.version).filter(writer => writer.ip_id === ipId);
  const drafts = useRows('/drafts', c.version).filter(draft => draft.ip_id === ipId);
  const [refs, setRefs] = useState<Row | null>(null);
  const [knowledgeRows, setKnowledgeRows] = useState<Row[] | null>(null);
  const [knowledgeError, setKnowledgeError] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [saved, setSaved] = useState<Row | null>(null);
  const [changes, setChanges] = useState<{ fields: WriterForm; version?: number } | null>(null);
  const [samples, setSamples] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api<Row>('/writers/reference').then(result => {
      if (active) setRefs(result);
    }).catch(() => {
      if (active) c.notify('写手参考库加载失败，请刷新重试');
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    api<Row[]>('/knowledge').then(result => {
      if (active) {
        setKnowledgeRows(result);
        setKnowledgeError(false);
      }
    }).catch(() => {
      if (active) setKnowledgeError(true);
    });
    return () => { active = false; };
  }, [c.version]);

  const selectedId = selected ?? (writers.find(writer => writer.is_default)?.id || writers.find(writer => writer.status !== 'archived')?.id || writers[0]?.id || '');
  const loaded = writers.find(writer => writer.id === selectedId);
  const current = saved && saved.id === selectedId && (!loaded || saved.version > loaded.version) ? saved : loaded;
  const choices = saved && !writers.some(writer => writer.id === saved.id) ? [saved, ...writers] : writers;
  const canManage = c.me.role !== 'member';
  const canEdit = !current || (current.status !== 'archived' && (canManage || current.created_by === c.me.id && !current.approved_by));
  const readyToRewrite = current?.status === 'active' && !changes;
  const data = changes?.fields ?? writerForm(current);
  const templates = (refs?.templates || []).filter((template: Row) => template.kind === 'content');
  const knowledge = verifiedKnowledge(knowledgeRows || []);
  const staleKnowledge = knowledgeRows && !knowledgeError ? data.knowledge_ids.filter(id => !knowledge.some(doc => doc.id === id)) : [];
  const matchedKnowledge: Row[] = current?.knowledge_sources || [];
  const noVerifiedKnowledge = knowledgeRows && !knowledgeError && knowledge.length === 0;
  const completed = data.answers.filter(answer => answer.trim()).length;
  const lengthLimit = data.answers[10];
  const validLimit = /^[0-9]+$/.test(lengthLimit) && Number(lengthLimit) >= 1 && Number(lengthLimit) <= 10000;
  const knowledgeChanged = data.knowledge_ids.join(',') !== (current?.knowledge_ids || []).join(',');

  function selectWriter(id: string) {
    setSelected(id);
    setChanges(null);
    setSamples(null);
  }

  function changeFields(patch: Partial<WriterForm>) {
    if (selected === null) setSelected(selectedId);
    setChanges(previous => ({ fields: { ...data, ...patch }, version: previous?.version ?? current?.version }));
  }

  function acceptWriter(writer: Row) {
    setSaved(writer);
    selectWriter(writer.id);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const generate = submitter?.value !== 'draft';
    if (busy || !canEdit || !data.name.trim()) return;
    if (generate && (!refs || completed !== 12 || !validLimit)) return;
    if (knowledgeChanged && staleKnowledge.length) return;

    const fields = {
      name: data.name,
      industry: data.industry,
      skill: data.skill,
      answers: data.answers,
      ...(!current || knowledgeChanged ? { knowledge_ids: data.knowledge_ids } : {}),
    };
    const version = changes?.version ?? current?.version;
    setBusy(true);
    try {
      const writer = await c.run(() => {
        if (generate) return post('/writers/generate', {
          ...fields,
          ip_id: ipId,
          ...(current ? { writer_id: current.id, expected_version: version } : {}),
        });
        if (current) return patch('/writers/' + current.id, { ...fields, version });
        return post('/writers', { ...fields, ip_id: ipId });
      }, generate ? (canManage ? 'IP Skill 已生成并启用' : 'IP Skill 已生成，等待审核') : '写手草稿已保存');
      if (writer) acceptWriter(writer);
    } finally {
      setBusy(false);
    }
  }

  async function examples() {
    setBusy(true);
    try {
      const result = await c.run(() => post('/writers/examples', {
        industry: data.industry, ip_id: ipId, skill: data.skill, answers: data.answers,
      }), '参考样例已生成，请核对后使用');
      if (result) setSamples(result);
    } finally {
      setBusy(false);
    }
  }

  async function command(action: string) {
    if (!current) return;
    setBusy(true);
    try {
      const writer = await c.run(() => post(`/writers/${current.id}/${action}`, { version: current.version }), '写手状态已更新');
      if (writer) acceptWriter(writer);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <Card title="IP Skill" className="ip-skill-card writer-detail">
      <form aria-label="IP Skill 配置" className="ip-skill-form" onSubmit={submit}>
        <div className="ip-skill-select">
          <Field label="专属写手">
            <select aria-label="专属写手" value={selectedId} disabled={busy} onChange={event => selectWriter(event.target.value)}>
              <option value="" disabled>新写手</option>
              {choices.map(writer => <option key={writer.id} value={writer.id}>{writer.name} · {STATUS[writer.status]}{writer.is_default ? ' · 默认' : ''}</option>)}
            </select>
          </Field>
          <button type="button" disabled={busy} onClick={() => selectWriter('')}><Plus size={16} />新建写手</button>
        </div>

        <div className="ip-skill-essentials">
          <Field label="写手名称">
            <input required disabled={!canEdit || busy} maxLength={80} value={data.name} onChange={event => changeFields({ name: event.target.value })} />
          </Field>
          <Field label="所属行业">
            <input disabled={!canEdit || busy} maxLength={100} value={data.industry} placeholder="例如：保险咨询" onChange={event => { changeFields({ industry: event.target.value }); setSamples(null); }} />
          </Field>
          <Field label="基础 Skill">
            <select aria-label="基础 Skill" disabled={!canEdit || busy || !refs} value={data.skill} onChange={event => { changeFields({ skill: event.target.value }); setSamples(null); }}>
              {templates.map((template: Row) => <option key={template.id} value={template.id}>{template.name}</option>)}
            </select>
          </Field>
        </div>
        {canEdit && <button type="button" disabled={busy || !data.industry.trim()} onClick={examples}><Sparkles size={14} />生成参考样例</button>}
        {samples && <div className="schema-preview">
          <Notice>{samples.notice} 仅填入空白回答，已有内容保留。</Notice>
          <details open><summary>查看12问参考样例</summary>{samples.answers.map((answer: string, index: number) => <p key={index}><strong>第{index + 1}问：</strong>{answer}</p>)}</details>
          <button type="button" onClick={() => {
            changeFields({ answers: data.answers.map((answer, index) => answer.trim() ? answer : samples.answers[index]) });
            setSamples(null);
          }}>仅填入空白项</button>
        </div>}

        <details className="ip-skill-section" key={current?.id || 'new'}>
          <summary><span>填写 12 问</span> <small>{completed}/12 已填写</small></summary>
          <p className="muted">没有的内容可填“无”，尚未核实的事实填“待核实”。</p>
          {!refs && <p className="muted">正在读取写手问题和基础模板。</p>}
          <div className="writer-questionnaire">
            {refs?.questions.map((question: string, index: number) => <Field key={index} label={`${index + 1}. ${question}`}>
              {index === 10
                ? <input type="number" min={1} max={10000} aria-label={`第${index + 1}问`} disabled={!canEdit || busy} value={data.answers[index]} onChange={event => changeFields({ answers: data.answers.map((answer, i) => i === index ? event.target.value : answer) })} />
                : <textarea rows={2} maxLength={5000} aria-label={`第${index + 1}问`} disabled={!canEdit || busy} value={data.answers[index]} onChange={event => changeFields({ answers: data.answers.map((answer, i) => i === index ? event.target.value : answer) })} />}
            </Field>)}
          </div>
        </details>

        <details className="ip-skill-section" key={'knowledge-' + (current?.id || 'new')}>
          <summary>
            <span>关联知识</span>{' '}
            <small>{data.knowledge_ids.length ? `已指定 ${data.knowledge_ids.length} 条` : '自动匹配'}</small>
            {current?.skill_text && <> <small>生成时匹配 {matchedKnowledge.length} 条</small></>}
          </summary>
          {matchedKnowledge.length > 0 && <>
            <p>生成时匹配的知识</p>
            <ul className="ip-skill-source-list" aria-label="本次生成匹配的知识">
              {matchedKnowledge.map(source => <li key={source.id}>
                <span>{source.title}</span>{source.category_path && <small>{source.category_path}</small>}
              </li>)}
            </ul>
          </>}
          <p className="muted">写作时自动匹配共享知识。可指定最多 {KNOWLEDGE_LIMIT} 条已核验资料，写作时优先引用。</p>
          {knowledgeError ? <p className="error-text">知识列表加载失败，请刷新重试。</p> : !knowledgeRows ? <p className="muted">正在加载知识。</p> : null}
          <div className="ip-skill-knowledge-list">
            {knowledge.map(doc => {
              const checked = data.knowledge_ids.includes(doc.id);
              return <label className="check-row" key={doc.id}>
                <input type="checkbox" checked={checked} disabled={!canEdit || busy || knowledgeError || (!checked && data.knowledge_ids.length >= KNOWLEDGE_LIMIT)} onChange={event => changeFields({ knowledge_ids: event.target.checked ? [...data.knowledge_ids, doc.id] : data.knowledge_ids.filter(id => id !== doc.id) })} />
                <span>{doc.title}{doc.category_path && <small className="muted">{doc.category_path}</small>}</span>
              </label>;
            })}
          </div>
          {staleKnowledge.length > 0 && <div className="button-row">
            <p className="muted">{staleKnowledge.length} 条已关联资料失效，写作时会跳过。</p>
            {canEdit && <button type="button" disabled={busy} onClick={() => changeFields({ knowledge_ids: data.knowledge_ids.filter(id => !staleKnowledge.includes(id)) })}>移除失效关联</button>}
          </div>}
        </details>

        {noVerifiedKnowledge && matchedKnowledge.length === 0 && <div className="ip-skill-knowledge-empty">
          <p className="muted">暂无有效的已核验知识，可继续使用填写的写手规则。</p>
          <button type="button" onClick={() => c.go('knowledge')}>去知识库核验</button>
        </div>}

        <div className="button-row ip-skill-footer">
          {canEdit && <>
            <button type="submit" className={readyToRewrite ? undefined : 'primary'} value="generate" disabled={busy || !refs || !data.name.trim() || completed !== 12 || !validLimit || (knowledgeChanged && staleKnowledge.length > 0)}><Sparkles size={15} />生成 IP Skill</button>
            {current?.status !== 'active' && <button type="submit" value="draft" disabled={busy || !refs || !data.name.trim() || (knowledgeChanged && staleKnowledge.length > 0)}>保存草稿</button>}
          </>}
          {canManage && current?.status === 'compiled' && !changes && <button type="button" className="primary" disabled={busy} onClick={() => command('activate')}>确认启用写手</button>}
          {current && <span className={'writer-status ' + current.status}>{STATUS[current.status]}</span>}
          {readyToRewrite && <button type="button" className="primary" onClick={() => onRewrite ? onRewrite(current.id) : c.go('materials')}>去选题库改写 <ArrowRight size={15} /></button>}
        </div>
        {current?.status === 'compiled' && <p className="muted">等待管理员或团队长启用后，即可用于改写。</p>}
        {current?.status === 'stale' && <p className="muted">配置已变更，重新生成后即可用于新任务。</p>}
      </form>
    </Card>
    {current && <WriterManagement key={current.id} c={c} writer={current} drafts={drafts} busy={busy} command={command} />}
  </>;
}

interface ManagementProps {
  c: Ctx;
  writer: Row;
  drafts: Row[];
  busy: boolean;
  command: (action: string) => Promise<void>;
}

function WriterManagement({ c, writer, drafts, busy, command }: ManagementProps) {
  const [rule, setRule] = useState<Row | null>(null);
  const [evidence, setEvidence] = useState<Row | null>(null);
  const [evidenceIds, setEvidenceIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const canManage = c.me.role !== 'member';
  const archived = writer.status === 'archived';
  const rules = (writer.rules || []).filter((entry: Row) => entry.status !== 'archived');
  const history: Row[] = writer.history || [];
  const disabled = busy || saving;
  const rulePath = `/writers/${writer.id}/rules`;

  async function updateRule(path: string, data: Row, message: string) {
    setSaving(true);
    try {
      return await c.run(() => post(path, data), message);
    } finally {
      setSaving(false);
    }
  }

  return <>
    <details className="ip-skill-management">
      <summary>查看 Skill 与管理</summary>
      <p className="muted">{writer.name} · v{writer.version}{writer.is_default ? ' · 当前IP默认' : ''}</p>
      <div className="button-row">
        {writer.skill_text && <>
          <button onClick={async () => {
            try { await navigator.clipboard.writeText(writer.skill_text); c.notify('SKILL已复制'); }
            catch { c.notify('复制失败，可选中文本或下载文件'); }
          }}><Copy size={14} />复制全文</button>
          <button onClick={() => saveText(writer.name + '-SKILL.md', writer.skill_text)}><Download size={14} />下载</button>
        </>}
        {canManage && writer.status === 'active' && !writer.is_default && <button disabled={disabled} onClick={() => command('default')}>设为默认写手</button>}
        {canManage && !archived && <button disabled={disabled} onClick={() => command('archive')}>归档写手</button>}
      </div>
      {writer.skill_text && <textarea className="context-text" aria-label="生成的SKILL" readOnly value={writer.skill_text} />}
      <details>
        <summary>历史版本 · {history.length}</summary>
        {[...history].reverse().map(entry => <details key={entry.version}>
          <summary>v{entry.version} · {entry.name} · {STATUS[entry.status]}</summary>
          <pre className="writer-history">{entry.skill_text || '该版本尚无SKILL'}</pre>
        </details>)}
      </details>
      <h3>语感规则</h3>
      {!archived && <button disabled={disabled} onClick={() => setRule({ kind: 'replace', source: 'manual', title: '', before: '', after: '' })}><Plus size={14} />加一条语感</button>}
      <div className="writer-rules">
        {!rules.length && <p className="muted">暂无语感规则。</p>}
        {rules.map((entry: Row) => <div key={entry.id}>
          <div className="writer-rule-title"><strong>{entry.title}</strong><span>{KINDS[entry.kind]} · {entry.source === 'manual' ? '人工偏好' : '改稿学习'} · {entry.status === 'adopted' ? '已采纳' : '候选'}</span></div>
          <p><del>{entry.before || '（无）'}</del> → <ins>{entry.after || '（删除）'}</ins></p>
          {canManage && !archived && <div className="button-row">
            {entry.status === 'candidate' && <button disabled={disabled} onClick={() => {
              if (entry.source === 'learned') { setEvidence(entry); setEvidenceIds([]); }
              else void updateRule(`${rulePath}/${entry.id}/adopt`, {}, '语感已采纳，请重新生成 Skill');
            }}>采纳语感</button>}
            <button disabled={disabled} onClick={() => updateRule(`${rulePath}/${entry.id}/archive`, {}, '语感已归档，请重新生成 Skill')}>归档语感</button>
          </div>}
        </div>)}
      </div>
    </details>
    {rule && <Modal title="新增语感规则" onClose={() => setRule(null)}>
      <form onSubmit={async event => {
        event.preventDefault();
        if (await updateRule(rulePath, rule, '已保存为候选语感')) setRule(null);
      }}>
        <div className="form-grid">
          <Field label="改动类型"><select value={rule.kind} onChange={event => setRule({ ...rule, kind: event.target.value })}>{Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
          <Field label="规则来源"><select value={rule.source} onChange={event => setRule({ ...rule, source: event.target.value })}><option value="manual">人工设定偏好</option><option value="learned">真实改稿学习（需3篇独立证据）</option></select></Field>
        </div>
        {[['规则说明', 'title'], ['改前例句', 'before'], ['改后例句', 'after']].map(([label, key]) => <Field key={key} label={label}>
          <textarea required={key === 'title'} maxLength={key === 'title' ? 200 : 1000} rows={2} value={rule[key]} onChange={event => setRule({ ...rule, [key]: event.target.value })} />
        </Field>)}
        <button className="primary" disabled={disabled}>保存候选语感</button>
      </form>
    </Modal>}
    {evidence && <Modal title="选择真实改稿证据" onClose={() => setEvidence(null)}>
      <p>至少3篇独立内容须保留改前和改后正文，同素材多版本只算一篇。</p>
      {drafts.filter(draft => draft.history?.some((entry: Row) => entry.body && entry.body !== draft.body)).map(draft => <label className="check-row" key={draft.id}>
        <input type="checkbox" checked={evidenceIds.includes(draft.id)} onChange={event => setEvidenceIds(event.target.checked ? [...evidenceIds, draft.id] : evidenceIds.filter(id => id !== draft.id))} />{draft.title} · v{draft.version}
      </label>)}
      <button className="primary" disabled={disabled || evidenceIds.length < 3} onClick={async () => {
        if (await updateRule(`${rulePath}/${evidence.id}/adopt`, { evidence_ids: evidenceIds }, '学习规律已采纳，请重新生成 Skill')) setEvidence(null);
      }}>确认采纳学习规律</button>
    </Modal>}
  </>;
}
