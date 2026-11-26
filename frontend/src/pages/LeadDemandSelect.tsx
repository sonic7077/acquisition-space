import type { Row } from '../api';
import { Field } from '../components/ui';

export function effectiveLeadsForIp(leads: Row[], ipId: string) {
  if (!ipId) return [];
  return leads.filter(lead => lead.effective_demand === true && (!lead.ip_id || lead.ip_id === ipId));
}

export default function LeadDemandSelect({
  leads,
  value,
  onChange,
  label = '关联需求',
  disabled = false,
  previous,
}: {
  leads: Row[];
  value: string;
  onChange: (id: string) => void;
  label?: string;
  disabled?: boolean;
  previous?: { id: string; label: string };
}) {
  return (
    <Field label="关联客户线索中的需求（可选）">
      <select aria-label={label} value={value} onChange={e => onChange(e.target.value)} disabled={disabled}>
        <option value="">不关联需求</option>
        {previous && !leads.some(lead => lead.id === previous.id) && (
          <option value={previous.id}>{previous.label}</option>
        )}
        {leads.map(lead => {
          const text = (lead.body || lead.title || '未记录原话').replace(/\s+/g, ' ').trim();
          const source = lead.work_title || lead.platform || '';
          return (
            <option key={lead.id} value={lead.id}>
              {lead.intent} · {text.length > 70 ? text.slice(0, 70) + '…' : text}
              {source ? ` · ${source}` : ''}{!lead.ip_id ? ' · 未归属IP' : ''}
            </option>
          );
        })}
      </select>
    </Field>
  );
}
