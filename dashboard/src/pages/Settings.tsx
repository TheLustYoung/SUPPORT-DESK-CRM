import {useEffect, useState} from 'react';
import {api} from '../services/api';
import {useT} from '../services/i18n';
import {Badge, Card, Empty, Field, LangText, Modal, Pills, Switch, confirmAction, useLookups, useName, useNotice, useResource} from '../components/ui';

type Tab = 'general' | 'languages' | 'departments' | 'statuses' | 'routing';

export function Settings() {
  const {t} = useT();
  const [tab, setTab] = useState<Tab>('general');
  const {data, reload} = useResource<any>('admin/settings');
  const {reload: reloadLookups} = useLookups();
  const refresh = () => { reload(); reloadLookups(); };
  return <div className="grid">
    <Pills value={tab} onChange={setTab} options={[['general', t('general')], ['languages', t('languages')], ['departments', t('departmentsCategories')],
      ['statuses', t('statuses')], ['routing', t('routing')]]}/>
    {!data ? <div className="card">{t('loading')}</div> : <>
      {tab === 'general' && <General data={data.general} onSaved={refresh}/>}
      {tab === 'languages' && <Languages data={data} onSaved={refresh}/>}
      {tab === 'departments' && <Departments data={data} onSaved={refresh}/>}
      {tab === 'statuses' && <Statuses data={data} onSaved={refresh}/>}
      {tab === 'routing' && <Routing data={data} onSaved={refresh}/>}
    </>}
  </div>;
}

const NUMBERS: [string, string, number, number][] = [
  ['reopen_days', 'reopenDays', 0, 365], ['auto_close_resolved_hours', 'autoClose', 0, 2160], ['messages_per_minute', 'perMinute', 1, 120],
  ['max_attachment_mb', 'maxAttachment', 1, 19], ['faq_max_depth', 'faqDepth', 1, 10], ['faq_max_children', 'faqChildren', 1, 100],
  ['faq_max_nodes', 'faqNodes', 10, 5000], ['campaign_rate_per_second', 'campaignRate', 1, 30]];
const RETENTION: [string, string][] = [['retention_events_days', 'retentionEvents'], ['retention_inactive_users_days', 'retentionUsers'], ['retention_closed_tickets_days', 'retentionFiles'], ['retention_logs_days', 'retentionLogs']];

function General({data, onSaved}: {data: any; onSaved: () => void}) {
  const {t} = useT();
  const [form, setForm] = useState<any>(data);
  const {run, view} = useNotice();
  useEffect(() => setForm(data), [data]);
  const set = (key: string, value: unknown) => setForm({...form, [key]: value});
  const changed = Object.fromEntries(Object.entries(form).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(data[k])));
  return <div className="grid two">
    <Card title={t('tickets_section')}>
      <div className="form-grid">
        <Field label={t('companyName')}><input type="text" value={form.company_name} maxLength={120} onChange={e => set('company_name', e.target.value)}/></Field>
        <Field label={t('ticketPrefix')}><input type="text" value={form.ticket_prefix} maxLength={12} onChange={e => set('ticket_prefix', e.target.value)}/></Field>
        {NUMBERS.map(([key, label, min, max]) => <Field key={key} label={t(label)}>
          <input type="number" min={min} max={max} value={form[key]} onChange={e => set(key, +e.target.value)}/></Field>)}
      </div>
    </Card>
    <div className="grid" style={{alignContent: 'start'}}>
      <Card title={t('bot_section')}>
        <Switch checked={form.ask_department} onChange={v => set('ask_department', v)} title={t('askDepartment')}/>
        <Switch checked={form.ask_category} onChange={v => set('ask_category', v)} title={t('askCategory')}/>
        <Switch checked={form.csat_enabled} onChange={v => set('csat_enabled', v)} title={t('csatEnabled')}/>
        <Switch checked={form.search_enabled} onChange={v => set('search_enabled', v)} title={t('searchEnabled')}/>
        <Switch checked={form.ask_language_first} onChange={v => set('ask_language_first', v)} title={t('askLanguageFirst')}/>
        <Field label={t('privacyText')}><LangText value={form.privacy_notice ?? {}} onChange={v => set('privacy_notice', v)} multiline rows={3} maxLength={1000}/></Field>
        <Field label={t('privacyUrl')}><input type="text" placeholder="https://" value={form.privacy_url ?? ''} onChange={e => set('privacy_url', e.target.value)}/></Field>
      </Card>
      <Card title={t('automation_section')} hint={t('automationHint')}>
        <div className="form-grid">
          <Field label={t('remindUserHours')}><input type="number" min={0} max={720} value={form.remind_user_hours} onChange={e => set('remind_user_hours', +e.target.value)}/></Field>
          <Field label={t('slaWarningPercent')}><input type="number" min={0} max={95} value={form.sla_warning_percent} onChange={e => set('sla_warning_percent', +e.target.value)}/></Field>
        </div>
        <Switch checked={form.weekly_report} onChange={v => set('weekly_report', v)} title={t('weeklyReport')} hint={t('weeklyReportHint')}/>
      </Card>
      <Card title={t('retention_section')}>
        <div className="form-grid">{RETENTION.map(([key, label]) => <Field key={key} label={t(label)}>
          <input type="number" min={0} max={3650} value={form[key]} onChange={e => set(key, +e.target.value)}/></Field>)}</div>
      </Card>
    </div>
    <div className="savebar"><span>{view ?? (Object.keys(changed).length ? t('unsaved') : '')}</span>
      <button className="btn-ghost" disabled={!Object.keys(changed).length} onClick={() => setForm(data)}>{t('reset')}</button>
      <button className="btn-primary" disabled={!Object.keys(changed).length} onClick={() => run(() => api('admin/settings/general', 'PATCH', changed).then(onSaved))}>{t('save')}</button></div>
  </div>;
}

function Languages({data, onSaved}: {data: any; onSaved: () => void}) {
  const {t} = useT();
  const [code, setCode] = useState(''), [custom, setCustom] = useState({code: '', name: '', native_name: '', rtl: false});
  const {run, view} = useNotice();
  const added = new Set(data.languages.map((l: any) => l.code));
  const patch = (lang: string, change: object) => run(() => api(`admin/settings/languages/${lang}`, 'PATCH', change).then(onSaved));
  return <Card title={t('languages')} hint={t('languagesHint')}>
    <div className="table-wrap"><table className="table">
      <thead><tr><th>{t('language')}</th><th>{t('nativeName')}</th><th>{t('rtlLang')}</th><th>{t('enabled')}</th><th/><th/></tr></thead>
      <tbody>{data.languages.map((l: any) => <tr key={l.code}>
        <td>{l.flag} <b>{l.code}</b> · {l.name}</td><td dir={l.rtl ? 'rtl' : 'ltr'}>{l.native_name}</td>
        <td><Switch checked={l.rtl} onChange={rtl => patch(l.code, {rtl})}/></td>
        <td><Switch checked={l.enabled} disabled={l.is_default} onChange={enabled => patch(l.code, {enabled})}/></td>
        <td>{l.is_default ? <Badge color="#c7a2c4">{t('default')}</Badge> : <button className="btn-ghost small" onClick={() => patch(l.code, {is_default: true})}>{t('makeDefault')}</button>}</td>
        <td>{!l.is_default && <button className="btn-ghost small danger" onClick={() => confirmAction(t('confirmDelete')) &&
          run(() => api(`admin/settings/languages/${l.code}`, 'DELETE').then(onSaved))}>{t('delete')}</button>}</td>
      </tr>)}</tbody></table></div>
    <div className="composer-foot">
      <select value={code} onChange={e => setCode(e.target.value)}><option value="">{t('addLanguage')}…</option>
        {Object.entries(data.known_languages).filter(([c]) => !added.has(c)).map(([c, l]: any) => <option key={c} value={c}>{l.flag} {l.native} · {l.name}</option>)}</select>
      <button className="btn-primary small" disabled={!code} onClick={() => run(() => api('admin/settings/languages', 'POST', {code}).then(() => { setCode(''); onSaved(); }))}>{t('add')}</button>
      <span className="grow">{view}</span>
    </div>
    <div className="composer-foot">
      <input type="text" placeholder={t('customCode')} value={custom.code} onChange={e => setCustom({...custom, code: e.target.value})}/>
      <input type="text" placeholder={t('name')} value={custom.name} onChange={e => setCustom({...custom, name: e.target.value})}/>
      <input type="text" placeholder={t('nativeName')} value={custom.native_name} onChange={e => setCustom({...custom, native_name: e.target.value})}/>
      <label className="inline"><input type="checkbox" checked={custom.rtl} onChange={e => setCustom({...custom, rtl: e.target.checked})}/>{t('rtlLang')}</label>
      <button className="btn-ghost small" disabled={!custom.code} onClick={() => run(() => api('admin/settings/languages', 'POST', custom)
        .then(() => { setCustom({code: '', name: '', native_name: '', rtl: false}); onSaved(); }))}>{t('add')}</button>
    </div>
  </Card>;
}

function Departments({data, onSaved}: {data: any; onSaved: () => void}) {
  const {t} = useT();
  const name = useName();
  const [edit, setEdit] = useState<any>(null);
  const {run, view} = useNotice();
  const save = () => run(() => {
    const kind = edit.kind === 'department' ? 'departments' : 'categories';
    const body = edit.kind === 'department' ? {name: edit.name, enabled: edit.enabled}
      : {name: edit.name, enabled: edit.enabled, department_id: edit.department_id, default_priority: edit.default_priority};
    return api(edit.id ? `admin/settings/${kind}/${edit.id}` : `admin/settings/${kind}`, edit.id ? 'PATCH' : 'POST', body).then(() => { setEdit(null); onSaved(); });
  });
  const reorder = (kind: string, ids: number[], index: number, delta: number) => {
    const copy = [...ids]; const [x] = copy.splice(index, 1); copy.splice(Math.max(0, Math.min(copy.length, index + delta)), 0, x);
    return run(() => api(`admin/settings/${kind}/reorder`, 'POST', {ids: copy}).then(onSaved));
  };
  return <Card title={t('departmentsCategories')} actions={<>
    <button className="btn-ghost small" onClick={() => setEdit({kind: 'department', name: {}, enabled: true})}>+ {t('addDepartment')}</button>
    <button className="btn-ghost small" onClick={() => setEdit({kind: 'category', name: {}, enabled: true, department_id: data.departments[0]?.id ?? null, default_priority: 'normal'})}>+ {t('addCategory')}</button>{view}</>}>
    {data.departments.map((d: any, i: number) => <div key={d.id} className="dept">
      <div className="rich-row"><b className={d.enabled ? '' : 'muted'}>{name(d.name)}</b>{!d.enabled && <Badge>{t('disabled')}</Badge>}<span className="grow"/>
        <button className="icon-btn small" onClick={() => reorder('departments', data.departments.map((x: any) => x.id), i, -1)}>↑</button>
        <button className="icon-btn small" onClick={() => setEdit({kind: 'department', ...d})}>✎</button>
        <button className="icon-btn small" onClick={() => confirmAction(t('confirmDelete')) && run(() => api(`admin/settings/departments/${d.id}`, 'DELETE').then(onSaved))}>✕</button></div>
      <div className="list">{data.categories.filter((c: any) => c.department_id === d.id).map((c: any) => <div key={c.id} className="list-row static">
        <span className={c.enabled ? '' : 'muted'}>{name(c.name)}<small>{t('defaultPriority')}: {t(c.default_priority)}</small></span>
        <span className="row-end"><button className="icon-btn small" onClick={() => setEdit({kind: 'category', ...c})}>✎</button>
          <button className="icon-btn small" onClick={() => confirmAction(t('confirmDelete')) && run(() => api(`admin/settings/categories/${c.id}`, 'DELETE').then(onSaved))}>✕</button></span>
      </div>)}</div>
    </div>)}
    {data.categories.some((c: any) => !c.department_id) && <div className="dept"><b>{t('noDepartment')}</b>
      {data.categories.filter((c: any) => !c.department_id).map((c: any) => <div key={c.id} className="list-row static"><span>{name(c.name)}</span>
        <button className="icon-btn small" onClick={() => setEdit({kind: 'category', ...c})}>✎</button></div>)}</div>}
    {edit && <Modal title={edit.kind === 'department' ? t('department') : t('category')} onClose={() => setEdit(null)}
                    footer={<button className="btn-primary" onClick={save}>{t('save')}</button>}>
      <div className="stack">
        <Field label={t('name')}><LangText value={edit.name} onChange={v => setEdit({...edit, name: v})} maxLength={80}/></Field>
        {edit.kind === 'category' && <>
          <Field label={t('department')}><select value={edit.department_id ?? ''} onChange={e => setEdit({...edit, department_id: e.target.value ? +e.target.value : null})}>
            <option value="">{t('noDepartment')}</option>{data.departments.map((d: any) => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
          <Field label={t('defaultPriority')}><select value={edit.default_priority} onChange={e => setEdit({...edit, default_priority: e.target.value})}>
            {['low', 'normal', 'high', 'urgent'].map(p => <option key={p} value={p}>{t(p)}</option>)}</select></Field></>}
        <Switch checked={edit.enabled} onChange={enabled => setEdit({...edit, enabled})} title={t('enabled')}/>
      </div>
    </Modal>}
  </Card>;
}

const STAGES = ['new', 'open', 'pending_agent', 'pending_user', 'escalated', 'resolved', 'closed', 'reopened'];

function Statuses({data, onSaved}: {data: any; onSaved: () => void}) {
  const {t} = useT();
  const name = useName();
  const [matrix, setMatrix] = useState<Record<string, string[]>>(data.transitions);
  const [edit, setEdit] = useState<any>(null);
  const {run, view} = useNotice();
  useEffect(() => setMatrix(data.transitions), [data.transitions]);
  const toggle = (from: string, to: string) => {
    const current = matrix[from] ?? [];
    setMatrix({...matrix, [from]: current.includes(to) ? current.filter(x => x !== to) : [...current, to]});
  };
  const save = () => run(() => (edit.isNew ? api('admin/settings/statuses', 'POST', edit)
    : api(`admin/settings/statuses/${edit.key}`, 'PATCH', {name: edit.name, color: edit.color, enabled: edit.enabled, notify_user: edit.notify_user}))
    .then(() => { setEdit(null); onSaved(); }));
  return <div className="grid">
    <Card title={t('statuses')} actions={<button className="btn-ghost small" onClick={() => setEdit({isNew: true, key: '', name: {}, color: '#c7a2c4', stage: 'open', enabled: true, notify_user: false})}>+ {t('addStatus')}</button>}>
      <div className="list">{data.statuses.map((s: any) => <div key={s.key} className="list-row static">
        <span><span className="dot" style={{background: s.color}}/><b>{name(s.name)}</b> <code>{s.key}</code><small>{t('stage')}: {t('stage_' + s.stage)}
          {s.notify_user ? ` · ${t('notifyUser')}` : ''}{s.builtin ? ` · ${t('builtinRole')}` : ''}</small></span>
        <span className="row-end">{!s.enabled && <Badge>{t('disabled')}</Badge>}<button className="icon-btn small" onClick={() => setEdit({...s})}>✎</button>
          {!s.builtin && <button className="icon-btn small" onClick={() => confirmAction(t('confirmDelete')) && run(() => api(`admin/settings/statuses/${s.key}`, 'DELETE').then(onSaved))}>✕</button>}</span>
      </div>)}</div>
    </Card>
    <Card title={t('statuses')} hint={t('transitionsHint')}>
      <div className="table-wrap"><table className="matrix"><thead><tr><th/>{data.statuses.map((s: any) => <th key={s.key}>{name(s.name)}</th>)}</tr></thead>
        <tbody>{data.statuses.map((from: any) => <tr key={from.key}><th>{name(from.name)}</th>{data.statuses.map((to: any) => <td key={to.key}>
          {from.key !== to.key && <input type="checkbox" checked={(matrix[from.key] ?? []).includes(to.key)} onChange={() => toggle(from.key, to.key)}
                                         aria-label={`${name(from.name)} → ${name(to.name)}`}/>}</td>)}</tr>)}</tbody></table></div>
      <div className="composer-foot"><span className="grow">{view}</span>
        <button className="btn-primary" onClick={() => run(() => api('admin/settings/transitions', 'PUT', matrix).then(onSaved))}>{t('save')}</button></div>
    </Card>
    {edit && <Modal title={t('status')} onClose={() => setEdit(null)} footer={<button className="btn-primary" onClick={save}>{t('save')}</button>}>
      <div className="stack">
        {edit.isNew && <Field label={t('statusKey')}><input type="text" value={edit.key} onChange={e => setEdit({...edit, key: e.target.value})}/></Field>}
        <Field label={t('name')}><LangText value={edit.name} onChange={v => setEdit({...edit, name: v})} maxLength={80}/></Field>
        {edit.isNew && <Field label={t('stage')}><select value={edit.stage} onChange={e => setEdit({...edit, stage: e.target.value})}>
          {STAGES.map(s => <option key={s} value={s}>{t('stage_' + s)}</option>)}</select></Field>}
        <Field label={t('color')}><input type="color" value={edit.color} onChange={e => setEdit({...edit, color: e.target.value})}/></Field>
        <Switch checked={edit.notify_user} onChange={notify_user => setEdit({...edit, notify_user})} title={t('notifyUser')}/>
        <Switch checked={edit.enabled} onChange={enabled => setEdit({...edit, enabled})} title={t('enabled')}/>
      </div>
    </Modal>}
  </div>;
}

function Routing({data, onSaved}: {data: any; onSaved: () => void}) {
  const {t} = useT();
  const name = useName();
  const {lookups} = useLookups();
  const [edit, setEdit] = useState<any>(null);
  const {run, view} = useNotice();
  const blank = {name: '', enabled: true, department_id: null, category_id: null, language: null, priority: null, set_department_id: null,
    set_priority: null, assign_to: null, strategy: 'none'};
  const num = (v: string) => v ? +v : null;
  const describe = (r: any) => [
    r.department_id && name(data.departments.find((d: any) => d.id === r.department_id)?.name), r.category_id && name(data.categories.find((c: any) => c.id === r.category_id)?.name),
    r.language, r.priority && t(r.priority)].filter(Boolean).join(' · ') || t('any');
  const outcome = (r: any) => [r.set_department_id && '→ ' + name(data.departments.find((d: any) => d.id === r.set_department_id)?.name),
    r.set_priority && t(r.set_priority), r.assign_to ? lookups.agents.find(a => a.id === r.assign_to)?.name : r.strategy !== 'none' && t('s_' + r.strategy)].filter(Boolean).join(' · ');
  const save = () => run(() => api(edit.id ? `admin/settings/rules/${edit.id}` : 'admin/settings/rules', edit.id ? 'PATCH' : 'POST', edit).then(() => { setEdit(null); onSaved(); }));
  return <Card title={t('routing')} hint={t('rulesHint')} actions={<button className="btn-ghost small" onClick={() => setEdit({...blank})}>+ {t('addRule')}</button>}>
    {data.rules.length ? <div className="list">{data.rules.map((r: any, i: number) => <div key={r.id} className="list-row static">
      <span><b>{i + 1}. {r.name}</b><small>{t('when')}: {describe(r)} — {t('then')}: {outcome(r) || '—'}</small></span>
      <span className="row-end">{!r.enabled && <Badge>{t('disabled')}</Badge>}
        <button className="icon-btn small" onClick={() => { const ids = data.rules.map((x: any) => x.id); const [x] = ids.splice(i, 1); ids.splice(Math.max(0, i - 1), 0, x);
          run(() => api('admin/settings/rules/reorder', 'POST', {ids}).then(onSaved)); }}>↑</button>
        <button className="icon-btn small" onClick={() => setEdit({...r})}>✎</button>
        <button className="icon-btn small" onClick={() => confirmAction(t('confirmDelete')) && run(() => api(`admin/settings/rules/${r.id}`, 'DELETE').then(onSaved))}>✕</button></span>
    </div>)}</div> : <Empty/>}
    {view}
    {edit && <Modal title={t('routing')} onClose={() => setEdit(null)} footer={<button className="btn-primary" disabled={!edit.name.trim()} onClick={save}>{t('save')}</button>}>
      <div className="form-grid">
        <Field label={t('name')} wide><input type="text" value={edit.name} onChange={e => setEdit({...edit, name: e.target.value})}/></Field>
        <b className="wide">{t('when')}</b>
        <Field label={t('department')}><select value={edit.department_id ?? ''} onChange={e => setEdit({...edit, department_id: num(e.target.value)})}><option value="">{t('any')}</option>
          {data.departments.map((d: any) => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
        <Field label={t('category')}><select value={edit.category_id ?? ''} onChange={e => setEdit({...edit, category_id: num(e.target.value)})}><option value="">{t('any')}</option>
          {data.categories.map((c: any) => <option key={c.id} value={c.id}>{name(c.name)}</option>)}</select></Field>
        <Field label={t('language')}><select value={edit.language ?? ''} onChange={e => setEdit({...edit, language: e.target.value || null})}><option value="">{t('any')}</option>
          {data.languages.map((l: any) => <option key={l.code} value={l.code}>{l.native_name}</option>)}</select></Field>
        <Field label={t('priority')}><select value={edit.priority ?? ''} onChange={e => setEdit({...edit, priority: e.target.value || null})}><option value="">{t('any')}</option>
          {['low', 'normal', 'high', 'urgent'].map(p => <option key={p} value={p}>{t(p)}</option>)}</select></Field>
        <b className="wide">{t('then')}</b>
        <Field label={t('setDepartment')}><select value={edit.set_department_id ?? ''} onChange={e => setEdit({...edit, set_department_id: num(e.target.value)})}><option value="">—</option>
          {data.departments.map((d: any) => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
        <Field label={t('setPriority')}><select value={edit.set_priority ?? ''} onChange={e => setEdit({...edit, set_priority: e.target.value || null})}><option value="">—</option>
          {['low', 'normal', 'high', 'urgent'].map(p => <option key={p} value={p}>{t(p)}</option>)}</select></Field>
        <Field label={t('assignTo')}><select value={edit.assign_to ?? ''} onChange={e => setEdit({...edit, assign_to: num(e.target.value)})}><option value="">—</option>
          {lookups.agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
        <Field label={t('strategy')}><select value={edit.strategy} disabled={!!edit.assign_to} onChange={e => setEdit({...edit, strategy: e.target.value})}>
          {['none', 'least_loaded', 'round_robin'].map(s => <option key={s} value={s}>{t('s_' + s)}</option>)}</select></Field>
        <Switch checked={edit.enabled} onChange={enabled => setEdit({...edit, enabled})} title={t('enabled')}/>
      </div>
    </Modal>}
  </Card>;
}
