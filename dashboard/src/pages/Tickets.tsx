import {useEffect, useRef, useState} from 'react';
import {api, download, query, upload} from '../services/api';
import {useT} from '../services/i18n';
import {Icon} from '../components/Icon';
import {Badge, Card, Empty, Field, Modal, Pager, Pills, PriorityBadge, StatusBadge, useAccess, useFormat, useLookups, useName, useNotice,
  useResource} from '../components/ui';

type Filters = {q: string; status: string; priority: string; department_id: string; assignee_id: string; language: string;
  date_from: string; date_to: string; overdue: boolean; mine: boolean};
const EMPTY: Filters = {q: '', status: '', priority: '', department_id: '', assignee_id: '', language: '', date_from: '', date_to: '', overdue: false, mine: false};

// Bot notifications are stored as Telegram HTML; staff see them as plain text (parsed inertly, never injected).
const plainText = (html: string) => new DOMParser().parseFromString(html, 'text/html').body.textContent ?? html;
const initials = (name: string | null | undefined) =>
  (name ?? '').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join('') || '?';

/** One SLA deadline: done (✓ with the time), running (time left until the deadline) or breached. */
function SlaStep({label, done, due, breached, date}: {label: string; done: string | null; due: string | null; breached: boolean;
  date: (value: string) => string}) {
  const state = done ? 'done' : breached ? 'breached' : due ? 'running' : 'none';
  return <div className={'sla-step ' + state}>
    <span className="sla-dot" aria-hidden="true">{done ? '✓' : breached ? '!' : ''}</span>
    <div><small>{label}</small><b>{done ? date(done) : due ? date(due) : '—'}</b></div>
  </div>;
}

export function Tickets({openId, setOpenId, openUser}: {openId: number | null; setOpenId: (id: number | null) => void; openUser: (id: number) => void}) {
  if (openId) return <TicketView id={openId} onBack={() => setOpenId(null)} openUser={openUser} openTicket={setOpenId}/>;
  return <TicketList onOpen={setOpenId}/>;
}

function DueCell({ticket}: {ticket: any}) {
  const {t} = useT();
  const {date} = useFormat();
  const done = ticket.resolved_at || ticket.closed_at;
  if (done) return <span className="muted">—</span>;
  const due = ticket.first_response_at ? ticket.due_resolution_at : ticket.due_response_at;
  const late = ticket.response_breached || ticket.resolution_breached;
  return <span className={late ? 'bad' : ''}>{date(due)}{late && <small> · {t('breached')}</small>}</span>;
}

function TicketList({onOpen}: {onOpen: (id: number) => void}) {
  const {t} = useT();
  const {lookups} = useLookups();
  const name = useName();
  const {date} = useFormat();
  const {can} = useAccess();
  const [filters, setFilters] = useState<Filters>(() => { try { return {...EMPTY, ...JSON.parse(sessionStorage.getItem('ticketFilters') ?? '{}')}; } catch { return EMPTY; } });
  const [page, setPage] = useState(1), [creating, setCreating] = useState(false);
  useEffect(() => { try { sessionStorage.setItem('ticketFilters', JSON.stringify(filters)); } catch {} setPage(1); }, [filters]);
  const {data, reload} = useResource<any>(`admin/tickets?${query({...filters, page, size: 30})}`, [filters, page]);
  useEffect(() => { const timer = setInterval(reload, 20000); return () => clearInterval(timer); }, [reload]);
  const set = (change: Partial<Filters>) => setFilters({...filters, ...change});

  return <div className="grid">
    <Card>
      <div className="filters">
        <input type="text" className="grow" placeholder={t('searchPlaceholder')} value={filters.q} onChange={e => set({q: e.target.value})}/>
        <select value={filters.status} onChange={e => set({status: e.target.value})} aria-label={t('status')}>
          <option value="">{t('status')}: {t('all')}</option>{lookups.statuses.map(s => <option key={s.key} value={s.key}>{name(s.name)}</option>)}</select>
        <select value={filters.priority} onChange={e => set({priority: e.target.value})} aria-label={t('priority')}>
          <option value="">{t('priority')}: {t('all')}</option>{lookups.priorities.map(p => <option key={p} value={p}>{t(p)}</option>)}</select>
        <select value={filters.department_id} onChange={e => set({department_id: e.target.value})} aria-label={t('department')}>
          <option value="">{t('department')}: {t('all')}</option><option value="none">{t('noDepartment')}</option>
          {lookups.departments.map(d => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select>
        <select value={filters.assignee_id} onChange={e => set({assignee_id: e.target.value})} aria-label={t('agent')}>
          <option value="">{t('agent')}: {t('all')}</option><option value="none">{t('unassigned')}</option>
          {lookups.agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
        <select value={filters.language} onChange={e => set({language: e.target.value})} aria-label={t('language')}>
          <option value="">{t('language')}: {t('all')}</option>{lookups.languages.map(l => <option key={l.code} value={l.code}>{l.flag} {l.native_name}</option>)}</select>
        <label className="inline">{t('from')}<input type="date" value={filters.date_from} onChange={e => set({date_from: e.target.value})}/></label>
        <label className="inline">{t('to')}<input type="date" value={filters.date_to} onChange={e => set({date_to: e.target.value})}/></label>
        <Pills value={filters.mine ? 'mine' : filters.overdue ? 'overdue' : 'all'} onChange={v => set({mine: v === 'mine', overdue: v === 'overdue'})}
               options={[['all', t('all')], ['mine', t('onlyMine')], ['overdue', t('onlyOverdue')]]}/>
        <button className="btn-ghost small" onClick={() => setFilters(EMPTY)}>{t('reset')}</button>
        {can('tickets.reply') && <button className="btn-primary small" onClick={() => setCreating(true)}>+ {t('newTicket')}</button>}
      </div>
    </Card>
    <Card>
      {data?.items?.length ? <div className="table-wrap"><table className="table">
        <thead><tr><th>№</th><th>{t('subject')}</th><th>{t('user')}</th><th>{t('status')}</th><th>{t('priority')}</th><th>{t('department')}</th>
          <th>{t('agent')}</th><th>{t('due')}</th><th>{t('updated')}</th></tr></thead>
        <tbody>{data.items.map((ticket: any) => <tr key={ticket.id} className="clickable" onClick={() => onOpen(ticket.id)}>
          <td><b>{ticket.number}</b></td><td className="ellipsis">{ticket.subject || '—'}</td><td>{ticket.user?.name}</td>
          <td><StatusBadge status={ticket.status}/></td><td><PriorityBadge priority={ticket.priority}/></td>
          <td>{name(lookups.departments.find(d => d.id === ticket.department_id)?.name) || '—'}</td>
          <td>{lookups.agents.find(a => a.id === ticket.assignee_id)?.name ?? <span className="muted">{t('unassigned')}</span>}</td>
          <td><DueCell ticket={ticket}/></td><td>{date(ticket.updated_at)}</td>
        </tr>)}</tbody></table></div> : <Empty/>}
      {data && <Pager page={page} size={30} total={data.total} onPage={setPage}/>}
    </Card>
    {creating && <NewTicket onClose={() => setCreating(false)} onCreated={id => { setCreating(false); onOpen(id); }}/>}
  </div>;
}

function NewTicket({onClose, onCreated}: {onClose: () => void; onCreated: (id: number) => void}) {
  const {t} = useT();
  const {lookups} = useLookups();
  const name = useName();
  const [form, setForm] = useState({user_id: '', subject: '', text: '', department_id: '', category_id: '', priority: ''});
  const {run, view} = useNotice();
  const submit = () => run(async () => {
    const result = await api('admin/tickets', 'POST', {...form, user_id: +form.user_id, department_id: form.department_id ? +form.department_id : null,
      category_id: form.category_id ? +form.category_id : null, priority: form.priority || null});
    onCreated(result.id);
  });
  return <Modal title={t('newTicket')} onClose={onClose} footer={<>{view}<button className="btn-primary" disabled={!form.user_id} onClick={submit}>{t('create')}</button></>}>
    <div className="form-grid">
      <Field label={t('ticketFor')}><input type="text" inputMode="numeric" value={form.user_id} onChange={e => setForm({...form, user_id: e.target.value.replace(/\D/g, '')})}/></Field>
      <Field label={t('subject')}><input type="text" value={form.subject} maxLength={200} onChange={e => setForm({...form, subject: e.target.value})}/></Field>
      <Field label={t('department')}><select value={form.department_id} onChange={e => setForm({...form, department_id: e.target.value, category_id: ''})}>
        <option value="">—</option>{lookups.departments.map(d => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
      <Field label={t('category')}><select value={form.category_id} onChange={e => setForm({...form, category_id: e.target.value})}>
        <option value="">—</option>{lookups.categories.filter(c => !form.department_id || c.department_id === +form.department_id)
          .map(c => <option key={c.id} value={c.id}>{name(c.name)}</option>)}</select></Field>
      <Field label={t('priority')}><select value={form.priority} onChange={e => setForm({...form, priority: e.target.value})}>
        <option value="">{t('default')}</option>{lookups.priorities.map(p => <option key={p} value={p}>{t(p)}</option>)}</select></Field>
      <Field label={t('firstMessage')} wide><textarea rows={4} value={form.text} onChange={e => setForm({...form, text: e.target.value})}/></Field>
    </div>
  </Modal>;
}

function TicketView({id, onBack, openUser, openTicket}: {id: number; onBack: () => void; openUser: (id: number) => void; openTicket: (id: number) => void}) {
  const {t, lang} = useT();
  const {lookups} = useLookups();
  const {can, me} = useAccess();
  const name = useName();
  const {date, size} = useFormat();
  const {data, reload, error} = useResource<any>(`admin/tickets/${id}`, [id]);
  const [tab, setTab] = useState<'conversation' | 'history'>('conversation');
  const [text, setText] = useState(''), [mode, setMode] = useState<'reply' | 'note'>('reply');
  const [asset, setAsset] = useState<any>(null), [comment, setComment] = useState('');
  const {run, view} = useNotice();
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { const timer = setInterval(reload, 10000); return () => clearInterval(timer); }, [reload]);
  useEffect(() => { bottom.current?.scrollIntoView({block: 'nearest'}); }, [data?.messages?.length]);
  if (error) return <div className="notice error">{t(error)}</div>;
  if (!data) return <div className="card">{t('loading')}</div>;

  const change = (what: string, value: unknown) => run(() => api(`admin/tickets/${id}/${what}`, 'POST', {value, comment}).then(() => { setComment(''); return reload(); }));
  const send = () => run(async () => {
    if (mode === 'note') await api(`admin/tickets/${id}/note`, 'POST', {text});
    else await api(`admin/tickets/${id}/reply`, 'POST', {text, asset_id: asset?.id ?? null});
    setText(''); setAsset(null);
    await reload();
  }, t(mode === 'note' ? 'saved' : 'delivery_queued'));
  const templates = lookups.templates.filter(tpl => !tpl.department_id || tpl.department_id === data.department_id);
  const insertTemplate = (tplId: string) => {
    const tpl = templates.find(x => String(x.id) === tplId);
    if (tpl) setText(text + (text ? '\n' : '') + (tpl.texts[data.language] || name(tpl.texts)));
  };
  const statusName = (key: string) => name(lookups.statuses.find(s => s.key === key)?.name) || key;
  const stage = lookups.statuses.find(s => s.key === data.status)?.stage ?? 'open';
  const eventValue = (kind: string, value: string | null) => {
    if (value === null || value === undefined || value === 'None') return '—';
    if (kind === 'status') return statusName(value);
    if (kind === 'priority') return t(value);
    if (kind === 'department') return name(lookups.departments.find(d => String(d.id) === value)?.name) || value;
    if (kind === 'category') return name(lookups.categories.find(c => String(c.id) === value)?.name) || value;
    if (kind === 'assignee') return lookups.agents.find(a => String(a.id) === value)?.name ?? value;
    return value;
  };

  return <div className="ticket-layout">
    <div className="grid">
      <Card title={<><button className="btn-ghost small" onClick={onBack}>← {t('back')}</button> {data.number} · {data.subject || '—'}</>}
            actions={<><StatusBadge status={data.status}/><PriorityBadge priority={data.priority}/>
              {data.reopen_count > 0 && <Badge>{t('reopened')} ×{data.reopen_count}</Badge>}</>}>
        <div className="ticket-controls">
          <Field label={t('status')}><select value={data.status} disabled={!can('tickets.update')} onChange={e => change('status', e.target.value)}>
            <option value={data.status}>{statusName(data.status)}</option>
            {data.allowed_statuses.map((s: string) => <option key={s} value={s}>{statusName(s)}</option>)}</select></Field>
          <Field label={t('priority')}><select value={data.priority} disabled={!can('tickets.update')} onChange={e => change('priority', e.target.value)}>
            {lookups.priorities.map(p => <option key={p} value={p}>{t(p)}</option>)}</select></Field>
          <Field label={t('transfer')}><select value={data.department_id ?? ''} disabled={!can('tickets.assign')} onChange={e => change('transfer', e.target.value || null)}>
            <option value="">{t('noDepartment')}</option>{lookups.departments.map(d => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
          <Field label={t('category')}><select value={data.category_id ?? ''} disabled={!can('tickets.update')} onChange={e => change('category', e.target.value || null)}>
            <option value="">—</option>{lookups.categories.filter(c => !data.department_id || !c.department_id || c.department_id === data.department_id)
              .map(c => <option key={c.id} value={c.id}>{name(c.name)}</option>)}</select></Field>
          <Field label={t('assignee')}><select value={data.assignee_id ?? ''} disabled={!can('tickets.assign')} onChange={e => change('assign', e.target.value || null)}>
            <option value="">{t('unassigned')}</option>{lookups.agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label={t('statusComment')}><input type="text" value={comment} maxLength={500} onChange={e => setComment(e.target.value)}/></Field>
        </div>
      </Card>

      <Card actions={<Pills value={tab} onChange={setTab} options={[['conversation', t('conversation')], ['history', t('history')]]}/>}>
        {tab === 'conversation' ? <div className="chat">
          {data.messages.map((m: any) => <div key={m.id} className={'bubble ' + m.direction}>
            <div className="bubble-head"><b>{m.direction === 'in' ? data.user?.name : m.direction === 'system' ? t('systemMessage') : m.author}</b>
              {m.direction === 'note' && <Badge color="#f2b866">{t('note')}</Badge>}<small>{date(m.created_at)}</small></div>
            {m.text && <div className="bubble-text" dir="auto">{m.direction === 'system' ? plainText(m.text) : m.text}</div>}
            {m.has_file && <button className="file-link" onClick={() => run(() => download(`admin/messages/${m.id}/file`), t('download'))}>
              📎 {m.file_name || t('file') + ' · ' + m.kind} {m.file_size ? '· ' + size(m.file_size) : ''}</button>}
            {(m.direction === 'out' || m.direction === 'system') && <small className={'delivery ' + m.delivery}>{t('delivery_' + m.delivery)}{m.error ? ` · ${m.error}` : ''}</small>}
          </div>)}
          <div ref={bottom}/>
        </div> : <div className="history">{data.events.map((e: any) => <div key={e.id} className="history-row">
          <small>{date(e.created_at)}</small><b>{e.actor_type === 'system' ? t('actor_system') : e.actor_name}</b>
          <span>{t('event_' + e.kind)}{e.kind !== 'created' && e.kind !== 'sla' && <>: {eventValue(e.kind, e.old)} → {eventValue(e.kind, e.new)}</>}
            {e.kind === 'sla' && <> ({e.new})</>}{e.comment && <i> · {e.comment}</i>}</span>
        </div>)}</div>}
      </Card>

      {(can('tickets.reply') || can('tickets.notes')) && <Card className="composer">
        <div className="composer-head">
          <Pills value={mode} onChange={setMode} options={[...(can('tickets.reply') ? [['reply', t('reply')]] : []),
            ...(can('tickets.notes') ? [['note', t('internalNote')]] : [])] as ['reply' | 'note', string][]}/>
          {mode === 'reply' && templates.length > 0 && <select value="" onChange={e => insertTemplate(e.target.value)} aria-label={t('insertTemplate')}>
            <option value="">{t('insertTemplate')}</option>{templates.map(tpl => <option key={tpl.id} value={tpl.id}>{tpl.shortcut ? `/${tpl.shortcut} · ` : ''}{tpl.name}</option>)}</select>}
        </div>
        <textarea rows={4} dir="auto" value={text} placeholder={mode === 'note' ? t('noteHint') : ''} maxLength={asset ? 1024 : 4096}
                  onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && (text.trim() || asset)) send(); }}/>
        <div className="composer-foot">
          {mode === 'reply' && <label className="btn-ghost small">📎 {asset ? asset.filename : t('attach')}
            <input type="file" hidden onChange={e => { const file = e.target.files?.[0]; if (file) run(() => upload('admin/media', file).then(setAsset), t('saved')); }}/></label>}
          {asset && <button className="btn-ghost small" onClick={() => setAsset(null)}>✕</button>}
          <span className="grow">{view}</span>
          <button className="btn-primary" disabled={!text.trim() && !asset} onClick={send}>{mode === 'note' ? t('save') : t('send')}</button>
        </div>
      </Card>}
    </div>

    <aside className="grid side">
      {data.user_profile && <Card className="person">
        <div className="person-head">
          <span className="person-avatar" aria-hidden="true">{initials(data.user_profile.name)}</span>
          <div><h2>{data.user_profile.name}</h2>{data.user_profile.username && <p className="hint">@{data.user_profile.username}</p>}</div>
        </div>
        <div className="person-actions">
          {can('tickets.reply') && <button className="circle" title={t('reply')} aria-label={t('reply')}
            onClick={() => { setMode('reply'); document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus(); }}><Icon name="chat"/></button>}
          {can('tickets.notes') && <button className="circle" title={t('internalNote')} aria-label={t('internalNote')}
            onClick={() => { setMode('note'); document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus(); }}><Icon name="note"/></button>}
          <button className="circle" title={t('history')} aria-label={t('history')} onClick={() => setTab('history')}><Icon name="clock"/></button>
          {can('users.view') && <button className="circle" title={t('open')} aria-label={t('open')} onClick={() => openUser(data.user_profile.id)}><Icon name="users"/></button>}
        </div>
        <dl className="facts">
          <dt>{t('telegramId')}</dt><dd>{data.user_profile.id}</dd>
          <dt>{t('chosenLanguage')}</dt><dd>{data.user_profile.language ?? '—'} / {data.user_profile.tg_language ?? '—'}</dd>
          <dt>{t('firstSeen')}</dt><dd>{date(data.user_profile.first_seen, false)}</dd>
          <dt>{t('lastSeen')}</dt><dd>{date(data.user_profile.last_seen)}</dd>
          {data.user_profile.phone && <><dt>{t('phone')}</dt><dd>{data.user_profile.phone}</dd></>}
          {data.user_profile.email && <><dt>{t('email')}</dt><dd>{data.user_profile.email}</dd></>}
        </dl>
        {data.user_profile.banned && <Badge color="#ff6b81">{t('banned')}</Badge>} {data.user_profile.bot_blocked && <Badge color="#f2b866">{t('botBlocked')}</Badge>}
      </Card>}

      <Card className="sla-card" title={t('sla')}>
        <div className="sla-steps">
          <SlaStep label={t('dueResponse')} done={data.first_response_at} due={data.due_response_at} breached={data.response_breached} date={date}/>
          <SlaStep label={t('dueResolution')} done={data.resolved_at} due={data.due_resolution_at} breached={data.resolution_breached} date={date}/>
        </div>
        <div className="sla-meta"><PriorityBadge priority={data.priority}/><span className="chip-soft">{data.language}</span>
          {data.rating && <span className="chip-soft">{'⭐'.repeat(data.rating)}</span>}</div>
        {data.rating_comment && <p className="hint">{data.rating_comment}</p>}
      </Card>

      {(can('tickets.assign') || can('tickets.update') || can('tickets.reopen')) && <Card className="next-card" title={t('nextStep')}>
        <div className="next-actions">
          {can('tickets.assign') && !data.assignee_id && <button className="btn-ghost" onClick={() => change('assign', me.id)}>{t('assignMe')}</button>}
          {can('tickets.update') && stage !== 'closed' && <button className="btn-dark" onClick={() => change('close', null)}>✓ {t('closeTicket')}</button>}
          {can('tickets.reopen') && (stage === 'closed' || stage === 'resolved') && <button className="btn-ghost" onClick={() => change('reopen', null)}>↻ {t('reopenTicket')}</button>}
        </div>
      </Card>}
      {data.other_tickets.length > 0 && <Card title={t('otherTickets')}>
        <div className="list">{data.other_tickets.map((o: any) => <button key={o.id} className="list-row" onClick={() => openTicket(o.id)}>
          <span><b>{o.number}</b> {o.subject}<small>{date(o.created_at, false)}</small></span><StatusBadge status={o.status}/></button>)}</div>
      </Card>}
      <small className="muted">{lang === 'ru' ? 'Ctrl+Enter — отправить' : 'Ctrl+Enter sends'}</small>
    </aside>
  </div>;
}
