import {useEffect, useMemo, useState} from 'react';
import {api} from '../services/api';
import {useT} from '../services/i18n';
import {Badge, Card, Empty, Field, LangText, Modal, Switch, confirmAction, useAccess, useFormat, useLookups, useName, useNotice,
  useResource} from '../components/ui';
import {PreviewButton, RichEditor, emptyRich, type Rich} from '../components/RichEditor';

type Node = {id: number; parent_id: number | null; position: number; enabled: boolean; title: Record<string, string>; content: Rich;
  keywords: Record<string, string>; redirect: string; redirect_value: string; allow_ticket: boolean; department_id: number | null;
  category_id: number | null; updated_by: string | null; updated_at: string | null};

export function Faq() {
  const {t} = useT();
  const {can} = useAccess();
  const name = useName();
  const {date} = useFormat();
  const {data, reload} = useResource<any>('admin/faq');
  const versions = useResource<any[]>('admin/faq/versions', [data?.published?.id]);
  const [selected, setSelected] = useState<number | null>(null), [note, setNote] = useState(''), [showVersions, setShowVersions] = useState(false);
  const [simulate, setSimulate] = useState(false);
  const {run, view} = useNotice();
  const nodes: Node[] = data?.nodes ?? [];
  const children = useMemo(() => {
    const map = new Map<number | null, Node[]>();
    nodes.forEach(n => map.set(n.parent_id, [...(map.get(n.parent_id) ?? []), n]));
    map.forEach(list => list.sort((a, b) => a.position - b.position || a.id - b.id));
    return map;
  }, [nodes]);
  if (!data) return <div className="card">{t('loading')}</div>;

  const add = (parent: number | null) => run(async () => {
    const created = await api('admin/faq/nodes', 'POST', {parent_id: parent, title: {[defaultLang(data)]: '…'}});
    await reload();
    setSelected(created.id);
  });
  const move = (node: Node, delta: number) => {
    const siblings = children.get(node.parent_id) ?? [];
    const index = siblings.findIndex(n => n.id === node.id);
    return run(() => api(`admin/faq/nodes/${node.id}/move`, 'POST', {parent_id: node.parent_id, position: Math.max(0, index + delta)}).then(reload));
  };
  const tree = (parent: number | null, depth: number): any => (children.get(parent) ?? []).map(node => <div key={node.id}>
    <div className={'tree-row' + (node.id === selected ? ' active' : '') + (node.enabled ? '' : ' off')} style={{paddingInlineStart: 8 + depth * 18}}>
      <button className="tree-title" onClick={() => setSelected(node.id)}>
        {(children.get(node.id)?.length ?? 0) > 0 ? '▾ ' : '• '}{name(node.title) || '—'}</button>
      {can('faq.edit') && <span className="tree-tools">
        <button onClick={() => move(node, -1)} aria-label={t('up')}>↑</button><button onClick={() => move(node, 1)} aria-label={t('down')}>↓</button>
        <button onClick={() => add(node.id)} aria-label={t('addChild')}>＋</button></span>}
    </div>
    {tree(node.id, depth + 1)}
  </div>);
  const node = nodes.find(n => n.id === selected) ?? null;

  return <div className="grid">
    <Card>
      <div className="publish-bar">
        {data.unpublished ? <span className="notice warn">{t('unpublished')}</span>
          : <span className="notice ok">{t('upToDate')}{data.published && ` · ${date(data.published.at)} · ${data.published.by}`}</span>}
        <span className="grow"/>
        <button className="btn-ghost small" onClick={() => setSimulate(true)}>{t('simulator')}</button>
        <button className="btn-ghost small" onClick={() => setShowVersions(true)}>{t('versions')}</button>
        {can('faq.publish') && <><input type="text" placeholder={t('publishNote')} value={note} onChange={e => setNote(e.target.value)}/>
          <button className="btn-primary" disabled={!data.unpublished}
                  onClick={() => run(() => api('admin/faq/publish', 'POST', {note}).then(() => { setNote(''); return reload(); }))}>{t('publish')}</button></>}
        {view}
      </div>
    </Card>
    <div className="faq-layout">
      <Card title={t('faq')} hint={t('limits', {d: data.limits.faq_max_depth, c: data.limits.faq_max_children, n: data.limits.faq_max_nodes})}
            actions={can('faq.edit') && <button className="btn-ghost small" onClick={() => add(null)}>+ {t('addRoot')}</button>}>
        {nodes.length ? <div className="tree">{tree(null, 0)}</div> : <Empty/>}
      </Card>
      {node ? <NodeEditor key={node.id} node={node} nodes={nodes} onSaved={reload} onDeleted={() => { setSelected(null); reload(); }}/>
        : <Card><Empty>{t('selectNode')}</Empty></Card>}
    </div>
    {showVersions && <Modal title={t('versions')} onClose={() => setShowVersions(false)}>
      {versions.data?.length ? <div className="list">{versions.data.map(v => <div key={v.id} className="list-row static">
        <span><b>#{v.id}</b> {v.note || '—'}<small>{date(v.published_at)} · {v.published_by} · {v.questions} {t('questions')}</small></span>
        {can('faq.publish') && <button className="btn-ghost small" onClick={() => confirmAction(t('restore') + '?') &&
          run(() => api(`admin/faq/versions/${v.id}/restore`, 'POST').then(() => { setShowVersions(false); setSelected(null); return reload(); }))}>{t('restore')}</button>}
      </div>)}</div> : <Empty/>}
    </Modal>}
    {simulate && <Simulator nodes={nodes} children={children} onClose={() => setSimulate(false)}/>}
  </div>;
}

const defaultLang = (data: any) => Object.keys(data.nodes?.[0]?.title ?? {})[0] ?? 'en';

function NodeEditor({node, nodes, onSaved, onDeleted}: {node: Node; nodes: Node[]; onSaved: () => void; onDeleted: () => void}) {
  const {t} = useT();
  const {can} = useAccess();
  const {lookups} = useLookups();
  const name = useName();
  const [form, setForm] = useState<Node>(() => ({...node, content: {blocks: node.content?.blocks ?? [], buttons: node.content?.buttons ?? []}}));
  const [target, setTarget] = useState<string>(node.parent_id === null ? '' : String(node.parent_id));
  const {run, view} = useNotice();
  useEffect(() => setTarget(node.parent_id === null ? '' : String(node.parent_id)), [node.parent_id]);
  const set = (change: Partial<Node>) => setForm({...form, ...change});
  const editable = can('faq.edit');
  const save = () => run(() => api(`admin/faq/nodes/${node.id}`, 'PATCH', {
    title: form.title, content: form.content, keywords: form.keywords, enabled: form.enabled, redirect: form.redirect,
    redirect_value: form.redirect_value, allow_ticket: form.allow_ticket, department_id: form.department_id, category_id: form.category_id}).then(onSaved));
  const descendants = (id: number): number[] => nodes.filter(n => n.parent_id === id).flatMap(n => [n.id, ...descendants(n.id)]);
  const blocked = new Set([node.id, ...descendants(node.id)]);

  return <Card title={name(node.title) || '—'} hint={node.updated_by ? `${node.updated_by}` : undefined}
               actions={<><PreviewButton nodeId={node.id}/>{!node.enabled && <Badge>{t('disabled')}</Badge>}</>}>
    <fieldset disabled={!editable} className="stack">
      <Field label={t('title')}><LangText value={form.title} onChange={title => set({title})} maxLength={64}/></Field>
      <Switch checked={form.enabled} onChange={enabled => set({enabled})} title={t('enabled')}/>
      <Field label={t('redirect')}><select value={form.redirect} onChange={e => set({redirect: e.target.value, redirect_value: ''})}>
        {['', 'node', 'url', 'support', 'menu'].map(r => <option key={r} value={r}>{t('r_' + r)}</option>)}</select></Field>
      {form.redirect === 'node' && <Field label={t('a_node')}><select value={form.redirect_value} onChange={e => set({redirect_value: e.target.value})}>
        <option value="">—</option>{nodes.filter(n => n.id !== node.id).map(n => <option key={n.id} value={String(n.id)}>{name(n.title)}</option>)}</select></Field>}
      {form.redirect === 'url' && <Field label={t('a_url')}><input type="text" placeholder="https://" value={form.redirect_value} onChange={e => set({redirect_value: e.target.value})}/></Field>}
      {(form.redirect === '' || form.redirect === 'url') && <Field label={t('answer')}><RichEditor value={form.content ?? emptyRich()} onChange={content => set({content})}/></Field>}
      <Field label={t('keywords')}><LangText value={form.keywords} onChange={keywords => set({keywords})} maxLength={500}/></Field>
      <Switch checked={form.allow_ticket} onChange={allow_ticket => set({allow_ticket})} title={t('allowTicket')}/>
      <div className="form-grid">
        <Field label={`${t('ticketsGoTo')}: ${t('department')}`}><select value={form.department_id ?? ''} onChange={e => set({department_id: e.target.value ? +e.target.value : null, category_id: null})}>
          <option value="">—</option>{lookups.departments.map(d => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select></Field>
        <Field label={t('category')}><select value={form.category_id ?? ''} onChange={e => set({category_id: e.target.value ? +e.target.value : null})}>
          <option value="">—</option>{lookups.categories.filter(c => !form.department_id || c.department_id === form.department_id)
            .map(c => <option key={c.id} value={c.id}>{name(c.name)}</option>)}</select></Field>
      </div>
    </fieldset>
    {editable && <div className="composer-foot">
      <select value={target} onChange={e => setTarget(e.target.value)} aria-label={t('moveTo')}>
        <option value="">{t('moveTo')}: {t('topLevel')}</option>
        {nodes.filter(n => !blocked.has(n.id)).map(n => <option key={n.id} value={String(n.id)}>{t('moveTo')}: {name(n.title)}</option>)}</select>
      <button className="btn-ghost small" disabled={target === (node.parent_id === null ? '' : String(node.parent_id))}
              onClick={() => run(() => api(`admin/faq/nodes/${node.id}/move`, 'POST', {parent_id: target ? +target : null, position: 999}).then(onSaved))}>{t('apply')}</button>
      <button className="btn-ghost small danger" onClick={() => confirmAction(t('deleteBranch')) && run(() => api(`admin/faq/nodes/${node.id}`, 'DELETE').then(onDeleted))}>{t('delete')}</button>
      <span className="grow">{view}</span>
      <button className="btn-primary" onClick={save}>{t('save')}</button>
    </div>}
  </Card>;
}

/** The draft as the bot would show it: topics as buttons, answers with their text, Back and Main menu. */
function Simulator({nodes, children, onClose}: {nodes: Node[]; children: Map<number | null, Node[]>; onClose: () => void}) {
  const {t} = useT();
  const {lookups} = useLookups();
  const [lang, setLang] = useState(lookups.languages.find(l => l.is_default)?.code ?? 'en');
  const [current, setCurrent] = useState<number | null>(null);
  const pick = (values: Record<string, string>) => values?.[lang] || values?.[lookups.languages.find(l => l.is_default)?.code ?? 'en'] || values?.en || Object.values(values ?? {})[0] || '';
  const node = nodes.find(n => n.id === current);
  const kids = (children.get(current) ?? []).filter(n => n.enabled);
  const rtl = lookups.languages.find(l => l.code === lang)?.rtl;
  const strip = (html: string) => html.replace(/<br\s*\/?>/g, '\n').replace(/<[^>]+>/g, '');
  return <Modal title={t('simulator')} onClose={onClose}>
    <select value={lang} onChange={e => setLang(e.target.value)}>{lookups.languages.filter(l => l.enabled).map(l => <option key={l.code} value={l.code}>{l.flag} {l.native_name}</option>)}</select>
    <div className="phone" dir={rtl ? 'rtl' : 'ltr'}>
      <div className="phone-msg">{node ? <>
        {(node.content?.blocks ?? []).filter(b => b.enabled !== false).map((b, i) => <div key={i}>
          {b.type !== 'text' && <div className="phone-media">{t('b_' + b.type)} · {b.assets.length}</div>}
          <p>{strip(pick(b.text))}</p></div>)}
        {!(node.content?.blocks ?? []).length && <p><b>{pick(node.title)}</b></p>}
      </> : <p>❓ <b>FAQ</b></p>}</div>
      <div className="phone-buttons">
        {kids.map(k => <button key={k.id} onClick={() => setCurrent(k.id)}>{pick(k.title)}</button>)}
        {node?.allow_ticket && !kids.length && <button>💬 {t('a_support')}</button>}
        {(node?.content?.buttons ?? []).map((b, i) => <button key={'b' + i} onClick={() => b.action === 'node' && setCurrent(+b.value)}>{pick(b.label)}</button>)}
        {node && <div className="row"><button onClick={() => setCurrent(node.parent_id)}>◀ {t('back')}</button><button onClick={() => setCurrent(null)}>🏠 {t('a_menu')}</button></div>}
      </div>
    </div>
  </Modal>;
}
