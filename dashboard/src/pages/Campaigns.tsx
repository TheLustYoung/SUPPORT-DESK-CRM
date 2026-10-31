import {useEffect, useState} from 'react';
import {api, query} from '../services/api';
import {useT} from '../services/i18n';
import {Badge, Card, Empty, Field, Pager, Pills, confirmAction, useAccess, useFormat, useLookups, useNotice, useResource} from '../components/ui';
import {RichEditor, emptyRich, type Rich} from '../components/RichEditor';

const STATUS_COLORS: Record<string, string> = {draft: '#8b8494', scheduled: '#7aa2ff', running: '#c7a2c4', paused: '#f2b866', done: '#5ad19a', cancelled: '#ff6b81'};

export function Campaigns() {
  const [open, setOpen] = useState<number | 'new' | null>(null);
  if (open !== null) return <CampaignEditor id={open === 'new' ? null : open} onBack={() => setOpen(null)} onSaved={id => setOpen(id)}/>;
  return <CampaignList onOpen={setOpen}/>;
}

function Progress({stats, total}: {stats: Record<string, number>; total: number}) {
  const parts: [string, string][] = [['sent', '#5ad19a'], ['blocked', '#f2b866'], ['failed', '#ff6b81'], ['skipped', '#8b8494']];
  return <div className="progress">{parts.map(([key, color]) => total ? <i key={key} style={{width: `${100 * (stats[key] ?? 0) / total}%`, background: color}}/> : null)}</div>;
}

function CampaignList({onOpen}: {onOpen: (id: number | 'new') => void}) {
  const {t} = useT();
  const {can} = useAccess();
  const {date} = useFormat();
  const {data, reload} = useResource<any[]>('admin/campaigns');
  useEffect(() => { const timer = setInterval(reload, 5000); return () => clearInterval(timer); }, [reload]);
  return <Card actions={can('campaigns.edit') && <button className="btn-primary small" onClick={() => onOpen('new')}>+ {t('newCampaign')}</button>}>
    {data?.length ? <div className="table-wrap"><table className="table">
      <thead><tr><th>{t('name')}</th><th>{t('status')}</th><th>{t('recipients')}</th><th>{t('d_sent')}</th><th/><th>{t('created')}</th></tr></thead>
      <tbody>{data.map(c => <tr key={c.id} className="clickable" onClick={() => onOpen(c.id)}>
        <td><b>{c.name}</b></td><td><Badge color={STATUS_COLORS[c.status]}>{t('c_' + c.status)}</Badge>{c.scheduled_at && <small> {date(c.scheduled_at)}</small>}</td>
        <td>{c.recipients || '—'}</td><td>{c.stats.sent} / {c.stats.blocked} / {c.stats.failed}</td>
        <td style={{width: 160}}><Progress stats={c.stats} total={c.recipients}/></td><td>{date(c.created_at)} · {c.created_by}</td>
      </tr>)}</tbody></table></div> : <Empty/>}
  </Card>;
}

type Filters = {languages: string[]; active_days?: number; inactive_days?: number; tags: string[]; exclude_tags: string[];
  registered_from?: string; registered_to?: string; has_tickets?: boolean};

function CampaignEditor({id, onBack, onSaved}: {id: number | null; onBack: () => void; onSaved: (id: number) => void}) {
  const {t, lang} = useT();
  const {can} = useAccess();
  const {lookups} = useLookups();
  const {date} = useFormat();
  const {data, reload} = useResource<any>(id ? `admin/campaigns/${id}` : null, [id]);
  const [name, setName] = useState(''), [content, setContent] = useState<Rich>(emptyRich());
  const [filters, setFilters] = useState<Filters>({languages: [], tags: [], exclude_tags: []});
  const [rate, setRate] = useState(20), [attempts, setAttempts] = useState(3), [count, setCount] = useState<number | null>(null);
  const [when, setWhen] = useState(''), [logStatus, setLogStatus] = useState(''), [logPage, setLogPage] = useState(1);
  const {run, view} = useNotice();
  const log = useResource<any>(id ? `admin/campaigns/${id}/deliveries?${query({status: logStatus, page: logPage})}` : null, [id, logStatus, logPage, data?.stats?.sent]);
  useEffect(() => {
    if (!data) return;
    setName(data.name); setContent({blocks: data.content.blocks ?? [], buttons: data.content.buttons ?? []});
    setFilters({languages: [], tags: [], exclude_tags: [], ...data.filters}); setRate(data.rate_per_second); setAttempts(data.max_attempts);
  }, [data?.id]);
  useEffect(() => { if (data && ['running', 'scheduled'].includes(data.status)) { const timer = setInterval(reload, 3000); return () => clearInterval(timer); } }, [data?.status]);
  const editable = !data || ['draft', 'scheduled'].includes(data.status);
  const body = {name, content, filters, rate_per_second: rate, max_attempts: attempts};
  const save = () => run(async () => {
    const saved = id ? await api(`admin/campaigns/${id}`, 'PUT', body) : await api('admin/campaigns', 'POST', body);
    if (!id) onSaved(saved.id); else await reload();
    return saved;
  });
  const control = (action: string, payload?: unknown) => run(() => api(`admin/campaigns/${id}/${action}`, 'POST', payload).then(() => reload()));
  const setF = (change: Partial<Filters>) => { setFilters({...filters, ...change}); setCount(null); };
  const list = (value: string) => value.split(',').map(x => x.trim().toLowerCase()).filter(Boolean);

  return <div className="grid">
    <Card title={<><button className="btn-ghost small" onClick={onBack}>← {t('back')}</button> {id ? name : t('newCampaign')}</>}
          actions={data && <Badge color={STATUS_COLORS[data.status]}>{t('c_' + data.status)}</Badge>}>
      <Field label={t('name')}><input type="text" value={name} maxLength={120} disabled={!editable} onChange={e => setName(e.target.value)}/></Field>
    </Card>
    <div className="grid two">
      <Card title={t('content')}>
        <fieldset disabled={!editable}><RichEditor value={content} onChange={setContent}/></fieldset>
      </Card>
      <div className="grid" style={{alignContent: 'start'}}>
        <Card title={t('segment')} hint={t('segmentHint')}>
          <fieldset disabled={!editable} className="form-grid">
            <Field label={t('languagesFilter')} wide><div className="chips">{lookups.languages.filter(l => l.enabled).map(l =>
              <label key={l.code} className="chip check"><input type="checkbox" checked={filters.languages.includes(l.code)}
                onChange={e => setF({languages: e.target.checked ? [...filters.languages, l.code] : filters.languages.filter(x => x !== l.code)})}/>{l.flag} {l.code}</label>)}</div></Field>
            <Field label={t('activeDays')}><input type="number" min={1} value={filters.active_days ?? ''} onChange={e => setF({active_days: e.target.value ? +e.target.value : undefined})}/></Field>
            <Field label={t('inactiveDays')}><input type="number" min={1} value={filters.inactive_days ?? ''} onChange={e => setF({inactive_days: e.target.value ? +e.target.value : undefined})}/></Field>
            <Field label={t('withTags')}><input type="text" value={filters.tags.join(', ')} onChange={e => setF({tags: list(e.target.value)})}/></Field>
            <Field label={t('withoutTags')}><input type="text" value={filters.exclude_tags.join(', ')} onChange={e => setF({exclude_tags: list(e.target.value)})}/></Field>
            <Field label={t('registeredFrom')}><input type="date" value={filters.registered_from ?? ''} onChange={e => setF({registered_from: e.target.value || undefined})}/></Field>
            <Field label={t('registeredTo')}><input type="date" value={filters.registered_to ?? ''} onChange={e => setF({registered_to: e.target.value || undefined})}/></Field>
            <Field label={t('hasTickets')}><select value={filters.has_tickets === undefined ? '' : String(filters.has_tickets)}
              onChange={e => setF({has_tickets: e.target.value === '' ? undefined : e.target.value === 'true'})}>
              <option value="">{t('any')}</option><option value="true">{t('withTickets')}</option><option value="false">{t('withoutTickets')}</option></select></Field>
            <Field label={t('rate')}><input type="number" min={1} max={30} value={rate} onChange={e => setRate(+e.target.value)}/></Field>
            <Field label={t('attempts')}><input type="number" min={1} max={10} value={attempts} onChange={e => setAttempts(+e.target.value)}/></Field>
          </fieldset>
          <div className="composer-foot">
            <button className="btn-ghost small" onClick={() => run(() => api('admin/campaigns/count', 'POST', {filters}).then(r => setCount(r.recipients)), t('saved'))}>{t('countRecipients')}</button>
            {count !== null && <b>{t('willReach', {n: count})}</b>}
          </div>
        </Card>

        <Card>
          <div className="stack">
            {editable && can('campaigns.edit') && <button className="btn-primary" disabled={!name.trim()} onClick={save}>{t('save')}</button>}
            {id && can('campaigns.edit') && <button className="btn-ghost" onClick={() => run(() => api(`admin/campaigns/${id}/test`, 'POST', {language: lang}), t('previewSent'))}>{t('testToMe')}</button>}
            {id && editable && can('campaigns.send') && <>
              <button className="btn-primary" onClick={async () => {
                const reach = (await api('admin/campaigns/count', 'POST', {filters})).recipients;
                if (confirmAction(t('startConfirm', {n: reach}))) control('start');
              }}>{t('startNow')}</button>
              <div className="inline-form"><input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)}/>
                <button className="btn-ghost" disabled={!when} onClick={() => control('schedule', {at: new Date(when).toISOString()})}>{t('schedule')}</button></div>
            </>}
            {data?.status === 'running' && can('campaigns.send') && <button className="btn-ghost" onClick={() => control('pause')}>{t('pause')}</button>}
            {data?.status === 'paused' && can('campaigns.send') && <button className="btn-ghost" onClick={() => control('resume')}>{t('resume')}</button>}
            {data && ['running', 'paused', 'scheduled'].includes(data.status) && can('campaigns.send') &&
              <button className="btn-ghost danger" onClick={() => confirmAction(t('cancel') + '?') && control('cancel')}>{t('cancel')}</button>}
            {data && (data.stats.failed > 0) && ['done', 'paused'].includes(data.status) && can('campaigns.send') &&
              <button className="btn-ghost" onClick={() => control('retry')}>{t('retryFailed')} ({data.stats.failed})</button>}
            {data && ['draft', 'cancelled'].includes(data.status) && can('campaigns.edit') &&
              <button className="btn-ghost danger" onClick={() => confirmAction(t('confirmDelete')) && run(() => api(`admin/campaigns/${id}`, 'DELETE').then(onBack))}>{t('delete')}</button>}
            {view}
          </div>
        </Card>

        {data && data.recipients > 0 && <Card title={t('deliveries')}>
          <Progress stats={data.stats} total={data.recipients}/>
          <div className="stats-line">{Object.entries(data.stats).map(([k, v]) => <span key={k}>{t('d_' + k)}: <b>{String(v)}</b></span>)}</div>
          {Object.keys(data.errors).length > 0 && <><b>{t('errorsByType')}</b><ul>{Object.entries(data.errors).map(([e, n]) => <li key={e}>{e}: {String(n)}</li>)}</ul></>}
          <Pills value={logStatus} onChange={v => { setLogStatus(v); setLogPage(1); }}
                 options={[['', t('all')], ['sent', t('d_sent')], ['failed', t('d_failed')], ['blocked', t('d_blocked')], ['pending', t('d_pending')]]}/>
          {log.data?.items?.length ? <table className="table compact"><tbody>{log.data.items.map((d: any) => <tr key={d.user_id}>
            <td>{d.name}</td><td>{d.language}</td><td>{t('d_' + d.status)}</td><td>{d.error}</td><td>{date(d.sent_at)}</td></tr>)}</tbody></table> : <Empty/>}
          {log.data && <Pager page={logPage} size={100} total={log.data.total} onPage={setLogPage}/>}
        </Card>}
      </div>
    </div>
  </div>;
}
