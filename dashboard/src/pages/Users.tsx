import {useEffect, useState} from 'react';
import {api, download, query, upload} from '../services/api';
import {useT} from '../services/i18n';
import {Badge, Card, Empty, Field, Modal, Pager, Pills, StatusBadge, confirmAction, useAccess, useFormat, useLookups, useNotice, useResource} from '../components/ui';

export function Users({openId, setOpenId, openTicket}: {openId: number | null; setOpenId: (id: number | null) => void; openTicket: (id: number) => void}) {
  if (openId) return <UserView id={openId} onBack={() => setOpenId(null)} openTicket={openTicket}/>;
  return <UserList onOpen={setOpenId}/>;
}

function UserList({onOpen}: {onOpen: (id: number) => void}) {
  const {t} = useT();
  const {lookups} = useLookups();
  const {date} = useFormat();
  const [q, setQ] = useState(''), [tag, setTag] = useState(''), [language, setLanguage] = useState(''), [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const {data} = useResource<any>(`admin/users?${query({q, tag, language, status, page, size: 50})}`, [q, tag, language, status, page]);
  return <div className="grid">
    <Card><div className="filters">
      <input type="text" className="grow" placeholder={t('searchPlaceholder')} value={q} onChange={e => { setQ(e.target.value); setPage(1); }}/>
      <input type="text" placeholder={t('tags')} value={tag} onChange={e => { setTag(e.target.value.toLowerCase()); setPage(1); }}/>
      <select value={language} onChange={e => { setLanguage(e.target.value); setPage(1); }} aria-label={t('language')}>
        <option value="">{t('language')}: {t('all')}</option>{lookups.languages.map(l => <option key={l.code} value={l.code}>{l.flag} {l.native_name}</option>)}</select>
      <Pills value={status} onChange={v => { setStatus(v); setPage(1); }}
             options={[['', t('status_all')], ['banned', t('status_banned')], ['blocked', t('status_blocked')], ['opt_out', t('status_opt_out')]]}/>
    </div></Card>
    <Card>
      {data?.items?.length ? <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('name')}</th><th>{t('telegramId')}</th><th>{t('language')}</th><th>{t('tags')}</th><th>{t('firstSeen')}</th><th>{t('lastSeen')}</th><th/></tr></thead>
        <tbody>{data.items.map((u: any) => <tr key={u.id} className="clickable" onClick={() => onOpen(u.id)}>
          <td><b>{u.name}</b>{u.username && <small className="muted"> @{u.username}</small>}</td><td>{u.id}</td>
          <td>{u.language ?? <span className="muted">{u.tg_language ?? '—'}</span>}</td><td>{u.tags.map((x: string) => <Badge key={x}>{x}</Badge>)}</td>
          <td>{date(u.first_seen, false)}</td><td>{date(u.last_seen)}</td>
          <td>{u.banned && <Badge color="#ff6b81">{t('banned')}</Badge>}{u.bot_blocked && <Badge color="#f2b866">{t('botBlocked')}</Badge>}
            {u.campaigns_opt_out && <Badge>{t('optOut')}</Badge>}{u.anonymized && <Badge>{t('anonymized')}</Badge>}</td>
        </tr>)}</tbody></table></div> : <Empty/>}
      {data && <Pager page={page} size={50} total={data.total} onPage={setPage}/>}
    </Card>
  </div>;
}

const plainText = (html: string) => new DOMParser().parseFromString(html, 'text/html').body.textContent ?? html;

/** Every message with this user: tickets they may see and direct messages, attachments, system notifications,
 *  delivery states; 100 at a time, older ones on request. */
function UserMessages({id, openTicket}: {id: number; openTicket: (id: number) => void}) {
  const {t} = useT();
  const {date} = useFormat();
  const {run, view} = useNotice();
  const [items, setItems] = useState<any[] | null>(null), [more, setMore] = useState(false);
  const load = (before?: number) => api<any[]>(`admin/users/${id}/messages${before ? `?before=${before}` : ''}`).then(rows => {
    setItems(old => [...(before ? old ?? [] : []), ...rows]);
    setMore(rows.length === 100);
  });
  useEffect(() => { setItems(null); load().catch(() => setItems([])); }, [id]);
  if (!items) return <div className="empty">{t('loading')}</div>;
  if (!items.length) return <Empty/>;
  return <div className="chat">
    {more && <button className="btn-ghost small" style={{alignSelf: 'center'}} onClick={() => run(() => load(items[items.length - 1].id))}>{t('loadOlder')}</button>}
    {[...items].reverse().map(m => <div key={m.id} className={'bubble ' + m.direction}>
      <div className="bubble-head"><b>{m.direction === 'in' ? t('fromUser') : m.direction === 'system' ? t('systemMessage') : m.author}</b>
        {m.ticket_id ? <button className="btn-ghost small" onClick={() => openTicket(m.ticket_id)}>{t('open')} #{m.ticket_id}</button> : <Badge>{t('directMessage')}</Badge>}
        <small>{date(m.created_at)}</small></div>
      {m.text && <div className="bubble-text" dir="auto">{m.direction === 'system' ? plainText(m.text) : m.text}</div>}
      {m.has_file && <button className="file-link" onClick={() => run(() => download(`admin/messages/${m.id}/file`))}>📎 {m.file_name || t('file') + ' · ' + m.kind}</button>}
      {m.direction !== 'in' && m.delivery && <small className={'delivery ' + m.delivery}>{t('delivery_' + m.delivery)}{m.error ? ` · ${m.error}` : ''}</small>}
    </div>)}
    {view}
  </div>;
}

type ContactFields = {email: string; phone: string; country: string; timezone: string};
const ZONES: string[] = (() => { try { return (Intl as any).supportedValuesOf('timeZone'); } catch { return []; } })();

/** Contacts the user gave in the conversation. Empty fields erase; the backend validates and audits. */
function ContactsEditor({user, onClose, onSaved}: {user: any; onClose: () => void; onSaved: () => void}) {
  const {t} = useT();
  const {run, view} = useNotice();
  const [form, setForm] = useState<ContactFields>({email: user.email ?? '', phone: user.phone ?? '', country: user.country ?? '', timezone: user.timezone ?? ''});
  const field = (key: keyof ContactFields, type = 'text') =>
    <Field label={t(key)}><input type={type} dir="ltr" value={form[key]} list={key === 'timezone' ? 'tz-list' : undefined}
                                 onChange={e => setForm({...form, [key]: e.target.value})}/></Field>;
  return <Modal title={t('editContacts')} onClose={onClose} footer={<>{view}<button className="btn-ghost" onClick={onClose}>{t('cancel')}</button>
    <button className="btn-primary" onClick={() => run(() => api(`admin/users/${user.id}/contacts`, 'PUT', form).then(onSaved), t('saved'))}>{t('save')}</button></>}>
    <p className="muted small">{t('contactsHint')}</p>
    <div className="form-grid">{field('phone', 'tel')}{field('email', 'email')}{field('country')}{field('timezone')}</div>
    <datalist id="tz-list">{ZONES.map(zone => <option key={zone} value={zone}/>)}</datalist>
  </Modal>;
}

function UserView({id, onBack, openTicket}: {id: number; onBack: () => void; openTicket: (id: number) => void}) {
  const {t} = useT();
  const {can} = useAccess();
  const {date, duration} = useFormat();
  const {data: user, reload, error} = useResource<any>(`admin/users/${id}`, [id]);
  const tickets = useResource<any[]>(can('tickets.view') ? `admin/users/${id}/tickets` : null, [id]);
  const timeline = useResource<any[]>(`admin/users/${id}/timeline`, [id]);
  const [tab, setTab] = useState<'tickets' | 'messages' | 'timeline'>('tickets');
  const [tag, setTag] = useState(''), [note, setNote] = useState(''), [message, setMessage] = useState(''), [asset, setAsset] = useState<any>(null);
  const [reason, setReason] = useState(''), [editing, setEditing] = useState(false);
  const {run, view} = useNotice();
  if (error) return <div className="notice error">{t(error)}</div>;
  if (!user) return <div className="card">{t('loading')}</div>;
  const act = (route: string, body?: unknown, done?: string) => run(() => api(`admin/users/${id}/${route}`, 'POST', body).then(() => reload()), done);
  const describe = (item: any) => item.type === 'message'
    ? `${t('msg_' + item.kind)}: ${item.data.text || '[' + item.data.media + ']'}` : t('ev_' + item.kind) + (item.data?.query ? ` «${item.data.query}»` : '');

  return <div className="ticket-layout">
    <div className="grid">
      <Card title={<><button className="btn-ghost small" onClick={onBack}>← {t('back')}</button> {user.name}</>}
            hint={user.username ? '@' + user.username : undefined}
            actions={<>{user.banned && <Badge color="#ff6b81">{t('banned')}</Badge>}{user.bot_blocked && <Badge color="#f2b866">{t('botBlocked')}</Badge>}
              {user.campaigns_opt_out && <Badge>{t('optOut')}</Badge>}{user.anonymized && <Badge>{t('anonymized')}</Badge>}</>}>
        <div className="kpis small">
          <div><small>{t('ticketsStats')}</small><b>{user.tickets.total}</b><span>{t('stage_open')} {user.tickets.open} · {t('stage_resolved')} {user.tickets.resolved} · {t('stage_closed')} {user.tickets.closed} · {t('stage_reopened')} {user.tickets.reopened}</span></div>
          <div><small>{t('avgResolution')}</small><b>{duration(user.avg_resolution_seconds)}</b></div>
          <div><small>{t('interactions')}</small><b>{user.interactions}</b><span>{t('messagesCount')}: {user.messages}</span></div>
        </div>
        <dl className="facts two">
          <dt>{t('telegramId')}</dt><dd>{user.id}</dd><dt>{t('tgLanguage')}</dt><dd>{user.tg_language ?? '—'}</dd>
          <dt>{t('chosenLanguage')}</dt><dd>{user.language ?? '—'}</dd><dt>{t('firstSeen')}</dt><dd>{date(user.first_seen)}</dd>
          <dt>{t('lastSeen')}</dt><dd>{date(user.last_seen)}</dd>
          {'phone' in user && <><dt>{t('phone')}</dt><dd>{user.phone ?? '—'}</dd><dt>{t('email')}</dt><dd>{user.email ?? '—'}</dd>
            <dt>{t('country')}</dt><dd>{user.country ?? '—'}</dd><dt>{t('timezone')}</dt><dd>{user.timezone ?? '—'}</dd></>}
          {user.banned && <><dt>{t('banReason')}</dt><dd>{user.ban_reason || '—'} · {user.banned_by} · {date(user.banned_at)}</dd></>}
        </dl>
        {'phone' in user && !user.anonymized && <div className="actions">
          <span className="muted small grow">{t('contactsHint')}</span>
          <button className="btn-ghost small" onClick={() => setEditing(true)}>{t('editContacts')}</button></div>}
        {editing && <ContactsEditor user={user} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }}/>}
      </Card>

      <Card actions={<Pills value={tab} onChange={setTab} options={[['tickets', t('tickets')], ['messages', t('conversation')], ['timeline', t('timeline')]]}/>}>
        {tab === 'messages' ? <UserMessages id={id} openTicket={openTicket}/>
          : tab === 'tickets' ? (tickets.data?.length ? <div className="list">{tickets.data.map(ticket =>
          <button key={ticket.id} className="list-row" onClick={() => openTicket(ticket.id)}>
            <span><b>{ticket.number}</b> {ticket.subject || '—'}<small>{date(ticket.created_at)}</small></span><StatusBadge status={ticket.status}/></button>)}</div> : <Empty/>)
          : (timeline.data?.length ? <div className="history">{timeline.data.map((item, i) => <div key={i} className="history-row">
            <small>{date(item.at)}</small><span dir="auto">{describe(item)}</span></div>)}</div> : <Empty/>)}
      </Card>

      {can('users.message') && !user.anonymized && <Card title={t('directMessage')} hint={t('directHint')}>
        <textarea rows={3} dir="auto" value={message} onChange={e => setMessage(e.target.value)} maxLength={asset ? 1024 : 4096}/>
        <div className="composer-foot">
          <label className="btn-ghost small">📎 {asset ? asset.filename : t('attach')}
            <input type="file" hidden onChange={e => { const file = e.target.files?.[0]; if (file) run(() => upload('admin/media', file).then(setAsset), t('saved')); }}/></label>
          <span className="grow">{view}</span>
          <button className="btn-primary" disabled={!message.trim() && !asset}
                  onClick={() => run(() => api(`admin/users/${id}/message`, 'POST', {text: message, asset_id: asset?.id ?? null})
                    .then(() => { setMessage(''); setAsset(null); }), t('delivery_queued'))}>{t('send')}</button>
        </div>
      </Card>}
    </div>

    <aside className="grid side">
      <Card title={t('tags')}>
        <div className="chips">{user.tags.map((x: string) => <span key={x} className="chip">{x}
          {can('users.edit') && <button onClick={() => run(() => api(`admin/users/${id}/tags/${encodeURIComponent(x)}`, 'DELETE').then(() => reload()))}>✕</button>}</span>)}</div>
        {can('users.edit') && <form className="inline-form" onSubmit={e => { e.preventDefault(); if (tag.trim()) act('tags', {tag}).then(() => setTag('')); }}>
          <input type="text" value={tag} placeholder={t('addTag')} onChange={e => setTag(e.target.value)}/><button className="btn-ghost small">{t('add')}</button></form>}
      </Card>
      <Card title={t('notes')}>
        {user.notes.map((n: any) => <div key={n.id} className="note"><small>{n.author} · {date(n.created_at)}</small><p dir="auto">{n.text}</p></div>)}
        {can('users.edit') && <><textarea rows={2} value={note} placeholder={t('addNote')} onChange={e => setNote(e.target.value)}/>
          <button className="btn-ghost small" disabled={!note.trim()} onClick={() => act('notes', {text: note}).then(() => setNote(''))}>{t('add')}</button></>}
      </Card>
      {user.faq.length > 0 && <Card title={t('faqPaths')}><ol>{user.faq.map((f: any) => <li key={f.node_id}>{f.title} · {f.count}</li>)}</ol></Card>}
      {user.campaigns.length > 0 && <Card title={t('campaignHistory')}><div className="list">{user.campaigns.map((c: any, i: number) =>
        <div key={i} className="list-row static"><span>#{c.campaign_id}<small>{date(c.sent_at)}</small></span><Badge>{t('d_' + c.status)}</Badge></div>)}</div></Card>}
      <Card>
        <div className="stack">
          {can('users.ban') && (user.banned
            ? <button className="btn-ghost" onClick={() => act('unban')}>{t('unban')}</button>
            : <><Field label={t('banReason')}><input type="text" value={reason} onChange={e => setReason(e.target.value)}/></Field>
              <button className="btn-ghost danger" onClick={() => act('ban', {reason})}>{t('ban')}</button></>)}
          {can('users.export') && <button className="btn-ghost" onClick={() => run(() => download(`admin/users/${id}/export`), t('download'))}>{t('export')}</button>}
          {can('users.delete') && !user.anonymized && <button className="btn-ghost danger"
            onClick={() => confirmAction(t('anonymizeConfirm')) && act('anonymize')}>{t('anonymize')}</button>}
          {view}
        </div>
      </Card>
    </aside>
  </div>;
}
