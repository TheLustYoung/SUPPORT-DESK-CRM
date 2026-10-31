import {useEffect, useState} from 'react';
import {api, upload} from '../services/api';
import {useT} from '../services/i18n';
import {Card, Empty, Field, LangText, Modal, Pills, Switch, confirmAction, useAccess, useFormat, useLookups, useName, useNotice,
  useResource} from '../components/ui';
import {PreviewButton, RichEditor, emptyRich, useFaqOptions, type Rich} from '../components/RichEditor';

type Tab = 'welcome' | 'menu' | 'texts' | 'media' | 'templates';

export function Content() {
  const {t} = useT();
  const {can} = useAccess();
  const tabs = ([['welcome', t('welcomeTab')], ['menu', t('menuTab')], ['texts', t('textsTab')], ['media', t('mediaTab')], ['templates', t('templatesTab')]] as [Tab, string][])
    .filter(([tab]) => tab === 'templates' ? can(['templates.edit']) : can('content.edit') || (tab === 'media' && can(['faq.edit', 'campaigns.edit'])));
  const [tab, setTab] = useState<Tab>(tabs[0]?.[0] ?? 'templates');
  return <div className="grid">
    <Pills value={tab} onChange={setTab} options={tabs}/>
    {tab === 'welcome' && <Welcome/>}
    {tab === 'menu' && <Menu/>}
    {tab === 'texts' && <Texts/>}
    {tab === 'media' && <Media/>}
    {tab === 'templates' && <Templates/>}
  </div>;
}

function Welcome() {
  const {t} = useT();
  const {data} = useResource<Rich>('admin/content/welcome');
  const [value, setValue] = useState<Rich>(emptyRich());
  const {run, view} = useNotice();
  useEffect(() => { if (data) setValue({blocks: data.blocks ?? [], buttons: data.buttons ?? []}); }, [data]);
  return <Card title={t('welcomeTab')} hint={t('welcomeHint')} actions={<PreviewButton content={value}/>}>
    <RichEditor value={value} onChange={setValue}/>
    <div className="composer-foot"><span className="grow">{view}</span>
      <button className="btn-primary" onClick={() => run(() => api('admin/content/welcome', 'PUT', value))}>{t('save')}</button></div>
  </Card>;
}

type MenuItem = {id?: number; action: string; value: string; label: Record<string, string>; content: Rich; enabled: boolean};

function Menu() {
  const {t} = useT();
  const {data} = useResource<MenuItem[]>('admin/content/menu');
  const [items, setItems] = useState<MenuItem[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const faqOptions = useFaqOptions();
  const {run, view} = useNotice();
  useEffect(() => { if (data) setItems(data.map(i => ({...i, content: {blocks: i.content?.blocks ?? [], buttons: i.content?.buttons ?? []}}))); }, [data]);
  const set = (i: number, change: Partial<MenuItem>) => setItems(items.map((item, j) => j === i ? {...item, ...change} : item));
  const move = (i: number, d: number) => { const copy = [...items]; const [x] = copy.splice(i, 1); copy.splice(Math.max(0, Math.min(copy.length, i + d)), 0, x); setItems(copy); };
  return <Card title={t('menuTab')} hint={t('menuHint')}>
    <div className="list">{items.map((item, i) => <div key={item.id ?? 'n' + i} className="menu-item">
      <div className="rich-row">
        <b>{t('m_' + item.action)}</b>
        <Switch checked={item.enabled} onChange={enabled => set(i, {enabled})}/>
        <span className="grow"/>
        <button className="icon-btn small" onClick={() => move(i, -1)} aria-label={t('up')}>↑</button>
        <button className="icon-btn small" onClick={() => move(i, 1)} aria-label={t('down')}>↓</button>
        <button className="icon-btn small" onClick={() => setItems(items.filter((_, j) => j !== i))} aria-label={t('delete')}>✕</button>
      </div>
      <LangText value={item.label} onChange={label => set(i, {label})} maxLength={64} placeholder={['faq', 'support', 'tickets', 'language', 'contacts'].includes(item.action) ? t('builtin') : t('buttonLabel')}/>
      {item.action === 'url' && <input type="text" placeholder="https://" value={item.value} onChange={e => set(i, {value: e.target.value})}/>}
      {item.action === 'faq_node' && <select value={item.value} onChange={e => set(i, {value: e.target.value})}>
        <option value="">—</option>{faqOptions.map(o => <option key={o.id} value={String(o.id)}>{o.label}</option>)}</select>}
      {item.action === 'content' && <button className="btn-ghost small" onClick={() => setOpen(i)}>{t('edit')}: {t('m_content')}</button>}
    </div>)}</div>
    <div className="composer-foot">
      <select value="" onChange={e => e.target.value && setItems([...items, {action: e.target.value, value: '', label: {}, content: emptyRich(), enabled: true}])}>
        <option value="">+ {t('addMenuItem')}</option>{['faq', 'support', 'tickets', 'language', 'contacts', 'faq_node', 'url', 'content'].map(a => <option key={a} value={a}>{t('m_' + a)}</option>)}</select>
      <span className="grow">{view}</span>
      <button className="btn-primary" onClick={() => run(() => api<MenuItem[]>('admin/content/menu', 'PUT', items).then(saved => setItems(saved)))}>{t('save')}</button>
    </div>
    {open !== null && <Modal wide title={t('m_content')} onClose={() => setOpen(null)} footer={<button className="btn-primary" onClick={() => setOpen(null)}>{t('apply')}</button>}>
      <RichEditor value={items[open].content} onChange={content => set(open, {content})}/></Modal>}
  </Card>;
}

function Texts() {
  const {t} = useT();
  const {lookups} = useLookups();
  const [lang, setLang] = useState(lookups.languages.find(l => l.is_default)?.code ?? 'en');
  const {data, setData} = useResource<any[]>(`admin/content/texts/${lang}`, [lang]);
  const [edits, setEdits] = useState<Record<string, string | null>>({}), [filter, setFilter] = useState('');
  const {run, view} = useNotice();
  useEffect(() => setEdits({}), [lang]);
  const rtl = lookups.languages.find(l => l.code === lang)?.rtl;
  const rows = (data ?? []).filter(r => !filter || (r.key + r.english + (r.custom ?? '') + r.builtin).toLowerCase().includes(filter.toLowerCase()));
  return <Card title={t('textsTab')} hint={t('textsHint')} actions={<>
    <select value={lang} onChange={e => setLang(e.target.value)}>{lookups.languages.map(l => <option key={l.code} value={l.code}>{l.flag} {l.native_name}</option>)}</select>
    <input type="text" placeholder={t('search')} value={filter} onChange={e => setFilter(e.target.value)}/></>}>
    <div className="table-wrap"><table className="table texts">
      <thead><tr><th>{t('key')}</th><th>{t('english')}</th><th>{t('yourText')}</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.key}>
        <td><code>{r.key}</code></td><td className="muted pre">{r.english}</td>
        <td><textarea rows={2} dir={rtl ? 'rtl' : 'auto'} placeholder={r.builtin || r.english} value={edits[r.key] ?? r.custom ?? ''}
                      onChange={e => setEdits({...edits, [r.key]: e.target.value})}/></td>
      </tr>)}</tbody></table></div>
    <div className="composer-foot"><span className="grow">{view}</span>
      <button className="btn-primary" disabled={!Object.keys(edits).length}
              onClick={() => run(() => api<any[]>(`admin/content/texts/${lang}`, 'PUT', edits).then(saved => { setData(saved); setEdits({}); }))}>{t('save')}</button></div>
  </Card>;
}

function Media() {
  const {t} = useT();
  const {can} = useAccess();
  const {date, size} = useFormat();
  const {data, reload} = useResource<any[]>('admin/media');
  const [kind, setKind] = useState('');
  const {run, view} = useNotice();
  return <Card title={t('mediaTab')} hint={t('mediaHint')} actions={<>
    <select value={kind} onChange={e => setKind(e.target.value)} aria-label={t('uploadAs')}><option value="">{t('uploadAs')}: {t('auto')}</option>
      {['photo', 'video', 'audio', 'voice', 'document', 'animation'].map(k => <option key={k} value={k}>{t('b_' + k)}</option>)}</select>
    <label className="btn-primary small">{t('upload')}<input type="file" hidden onChange={e => {
      const file = e.target.files?.[0];
      if (file) run(() => upload('admin/media', file, kind ? {kind} : {}).then(reload));
      e.target.value = '';
    }}/></label>{view}</>}>
    {data?.length ? <div className="media-grid">{data.map(a => <div key={a.id} className="media-tile">
      {a.kind === 'photo' ? <img src={`/api/admin/media/${a.id}/file`} alt=""/> : <span className="media-kind">{t('b_' + a.kind)}</span>}
      <small>{a.filename}<br/>{size(a.size)} · {date(a.created_at, false)}</small>
      {can('content.edit') && <button className="btn-ghost small danger" onClick={() => confirmAction(t('confirmDelete')) &&
        run(() => api(`admin/media/${a.id}`, 'DELETE').then(reload))}>{t('delete')}</button>}
    </div>)}</div> : <Empty/>}
  </Card>;
}

type Template = {id?: number; name: string; shortcut: string; texts: Record<string, string>; department_id: number | null; enabled: boolean; position: number};

function Templates() {
  const {t} = useT();
  const {can} = useAccess();
  const {lookups, reload: reloadLookups} = useLookups();
  const name = useName();
  const {data, reload} = useResource<Template[]>('admin/templates');
  const [edit, setEdit] = useState<Template | null>(null);
  const {run, view} = useNotice();
  const save = () => edit && run(() => api(edit.id ? `admin/templates/${edit.id}` : 'admin/templates', edit.id ? 'PUT' : 'POST', edit)
    .then(() => { setEdit(null); reloadLookups(); return reload(); }));
  return <Card title={t('templatesTab')} actions={can('templates.edit') && <button className="btn-primary small"
    onClick={() => setEdit({name: '', shortcut: '', texts: {}, department_id: null, enabled: true, position: 0})}>+ {t('newTemplate')}</button>}>
    {data?.length ? <div className="list">{data.map(tpl => <button key={tpl.id} className="list-row" onClick={() => can('templates.edit') && setEdit(tpl)}>
      <span><b>{tpl.name}</b>{tpl.shortcut && <code> /{tpl.shortcut}</code>}<small>{name(tpl.texts).slice(0, 120)}</small></span>
      <span className="row-end">{tpl.department_id && name(lookups.departments.find(d => d.id === tpl.department_id)?.name)}{!tpl.enabled && ` · ${t('disabled')}`}</span>
    </button>)}</div> : <Empty/>}
    {view}
    {edit && <Modal title={edit.id ? edit.name : t('newTemplate')} onClose={() => setEdit(null)} footer={<>
      {edit.id && <button className="btn-ghost danger" onClick={() => confirmAction(t('confirmDelete')) &&
        run(() => api(`admin/templates/${edit.id}`, 'DELETE').then(() => { setEdit(null); reloadLookups(); return reload(); }))}>{t('delete')}</button>}
      <button className="btn-primary" disabled={!edit.name.trim()} onClick={save}>{t('save')}</button></>}>
      <div className="form-grid">
        <Field label={t('name')}><input type="text" value={edit.name} maxLength={80} onChange={e => setEdit({...edit, name: e.target.value})}/></Field>
        <Field label={t('shortcut')}><input type="text" value={edit.shortcut} maxLength={32} onChange={e => setEdit({...edit, shortcut: e.target.value})}/></Field>
        <Field label={t('department')}><select value={edit.department_id ?? ''} onChange={e => setEdit({...edit, department_id: e.target.value ? +e.target.value : null})}>
          <option value="">{t('all')}</option>{lookups.departments.map(d => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
        <Switch checked={edit.enabled} onChange={enabled => setEdit({...edit, enabled})} title={t('enabled')}/>
        <Field label={t('templateText')} wide><LangText value={edit.texts} onChange={texts => setEdit({...edit, texts})} multiline rows={6} maxLength={4096}/></Field>
      </div>
    </Modal>}
  </Card>;
}
