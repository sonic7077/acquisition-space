import { useEffect, useState } from 'react';
import { api, type Row } from '../api';
import './WorkCommentPicker.css';

type WorkCommentPickerProps = {
  materialId?: string;
  source?: Row;
  sourceRestricted?: boolean;
  version: number;
  ipId: string;
  leads: Row[];
  selected: string[];
  disabled: boolean;
  initiallyOpen?: boolean;
  onChange: (ids: string[]) => void;
};

export default function WorkCommentPicker({materialId,source,sourceRestricted,version,ipId,leads,selected,disabled,initiallyOpen,onChange}:WorkCommentPickerProps) {
  const [lookup,setLookup] = useState<{ id: string; source?: Row; failed?: boolean }>();
  useEffect(() => {
    if (!materialId || source || sourceRestricted) return;
    const controller = new AbortController();
    api<Row[]>('/records/materials', { signal: controller.signal })
      .then(rows => {
        if (!controller.signal.aborted) setLookup({ id: materialId, source: rows.find(row => row.id === materialId) });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLookup({ id: materialId, failed: true });
      });
    return () => controller.abort();
  }, [materialId, source, sourceRestricted, version]);

  const resolved = lookup?.id === materialId ? lookup : undefined;
  const work = sourceRestricted ? undefined : source || resolved?.source;
  const comments = work ? leads.filter(lead => lead.material_id === work.id && (!lead.ip_id || lead.ip_id === ipId)) : [];
  const selectedCount = comments.filter(lead => selected.includes(lead.id)).length;
  const missingSourceMessage = sourceRestricted || resolved && !resolved.failed ? '原作品当前不可访问。'
    : !materialId ? '此稿件尚未关联原作品。'
    : resolved?.failed ? '原作品读取失败，请刷新后重试。' : '正在读取原作品…';

  return <section aria-label="原作品评论" className="work-comments">
    <details open={initiallyOpen}>
      <summary>原作品评论 · 已入库 {comments.length} 条 · 已选 {selectedCount} 条</summary>
      {work ? <>
        <p className="work-comments-source">
          <strong>{work.title}</strong>
          {work.platform && <span className="badge">{work.platform}</span>}
          {work.source?.startsWith('https://') && <a href={work.source} target="_blank" rel="noreferrer">查看原作品</a>}
        </p>
        {comments.length ? <div className="rewrite-comments">
          {comments.map(lead => <label key={lead.id} className="check-row work-comment">
            <input type="checkbox" disabled={disabled} checked={selected.includes(lead.id)} onChange={event => {
              onChange(event.target.checked ? [...selected,lead.id] : selected.filter(id => id !== lead.id));
            }}/>
            <span>
              {lead.title && lead.body && <strong>{lead.title}</strong>}
              <span>{lead.body || lead.title}</span>
            </span>
          </label>)}
        </div> : <p className="muted">当前作品暂无已入库评论。</p>}
      </> : <p className="muted">{missingSourceMessage}</p>}
    </details>
  </section>;
}
