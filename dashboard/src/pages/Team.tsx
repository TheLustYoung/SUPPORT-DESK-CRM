import {useState} from 'react';
import {api} from '../services/api';
import {useT} from '../services/i18n';
import {Badge, Card, Field, Modal, Pills, Switch, confirmAction, useFormat, useLookups, useName, useNotice, useResource} from '../components/ui';

export function Team() {
  const {t} = useT();
  const {date} = useFormat();
  const name = useName();
  const {lookups, reload: reloadLookups} = useLookups();
  const {data, reload} = useResource<any>('admin/team');
  const [tab, setTab] = useState<'accounts' | 'roles'>('accounts');
  const [account, setAccount] = useState<any>(null), [role, setRole] = useState<any>(null);
  const {run, view} = useNotice();
  if (!data) return <div className="card">{t('loading')}</div>;
  const roleName = (id: string) => data.roles.find((r: any) => r.id === id)?.name ?? id;
  const refresh = () => { reload(); reloadLookups(); };
  const saveAccount = () => run(async () => {
    const {id, isNew, ...body} = account;
    if (isNew) await api('admin/team/accounts', 'POST', body);
    else {
      const change = {...body};
      if (!change.password) delete change.password;
      await api(`admin/team/accounts/${id}`, 'PATCH', change);
    }
    setAccount(null);
    refresh();
  });
  const saveRole = () => run(() => api(role.id ? `admin/team/roles/${role.id}` : 'admin/team/roles', role.id ? 'PUT' : 'POST',
    {name: role.name, description: role.description, permissions: role.permissions}).then(() => { setRole(null); refresh(); }));

  return <div className="grid">
    <Pills value={tab} onChange={setTab} options={[['accounts', t('accounts')], ['roles', t('roles')]]}/>
    {tab === 'accounts' ? <Card actions={<button className="btn-primary small" onClick={() => setAccount({isNew: true, username: '', password: '', role: 'agent',
      display_name: '', email: '', departments: [], languages: [], max_open_tickets: 0})}>+ {t('newAccount')}</button>}>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>{t('username')}</th><th>{t('role')}</th><th>{t('departments')}</th><th>{t('speaks')}</th><th>{t('lastLogin')}</th><th/></tr></thead>
        <tbody>{data.accounts.map((a: any) => <tr key={a.id} className="clickable" onClick={() => setAccount({...a, password: ''})}>
          <td><b>{a.display_name || a.username}</b> <small className="muted">{a.username}</small></td><td>{roleName(a.role)}</td>
          <td>{a.departments.map((d: number) => name(lookups.departments.find(x => x.id === d)?.name)).join(', ') || '—'}</td>
          <td>{a.languages.join(', ') || '—'}</td><td>{date(a.last_login_at)}</td>
          <td>{!a.active && <Badge color="#ff6b81">{t('disabled')}</Badge>}{a.locked && <Badge color="#f2b866">{t('locked')}</Badge>}
            {a.totp && <Badge color="#5ad19a">{t('twoFactor')}</Badge>}{a.telegram && <Badge>{t('telegramLinked')}</Badge>}</td>
        </tr>)}</tbody></table></div>
    </Card> : <Card actions={<button className="btn-primary small" onClick={() => setRole({name: '', description: '', permissions: []})}>+ {t('newRole')}</button>}>
      <div className="list">{data.roles.map((r: any) => <button key={r.id} className="list-row" onClick={() => setRole({...r})}>
        <span><b>{r.name}</b>{r.builtin && <Badge>{t('builtinRole')}</Badge>}<small>{r.description}</small></span>
        <span className="row-end">{r.accounts} {t('accountsCount')} · {r.permissions.length} {t('permissions').toLowerCase()}</span></button>)}</div>
    </Card>}
    {view}

    {account && <Modal title={account.isNew ? t('newAccount') : account.username} onClose={() => setAccount(null)} footer={<>
      {!account.isNew && account.id !== data.you && <button className="btn-ghost danger" onClick={() => confirmAction(t('confirmDelete')) &&
        run(() => api(`admin/team/accounts/${account.id}`, 'DELETE').then(() => { setAccount(null); refresh(); }))}>{t('delete')}</button>}
      <button className="btn-primary" onClick={saveAccount}>{t('save')}</button></>}>
      <div className="form-grid">
        {account.isNew && <Field label={t('username')}><input type="text" value={account.username} onChange={e => setAccount({...account, username: e.target.value})}/></Field>}
        <Field label={account.isNew ? t('password') : t('newPassword')}><input type="password" autoComplete="new-password" value={account.password}
          placeholder={t('passwordRule')} onChange={e => setAccount({...account, password: e.target.value})}/></Field>
        <Field label={t('displayName')}><input type="text" value={account.display_name} onChange={e => setAccount({...account, display_name: e.target.value})}/></Field>
        <Field label={t('email')}><input type="text" value={account.email} onChange={e => setAccount({...account, email: e.target.value})}/></Field>
        <Field label={t('role')}><select value={account.role} disabled={account.id === data.you} onChange={e => setAccount({...account, role: e.target.value})}>
          {data.roles.map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
        <Field label={t('maxOpen')}><input type="number" min={0} value={account.max_open_tickets} onChange={e => setAccount({...account, max_open_tickets: +e.target.value})}/></Field>
        <Field label={t('departments')} wide><div className="chips">{lookups.departments.map(d => <label key={d.id} className="chip check">
          <input type="checkbox" checked={account.departments.includes(d.id)} onChange={e => setAccount({...account,
            departments: e.target.checked ? [...account.departments, d.id] : account.departments.filter((x: number) => x !== d.id)})}/>{name(d.name)}</label>)}</div></Field>
        <Field label={t('speaks')} wide><div className="chips">{lookups.languages.map(l => <label key={l.code} className="chip check">
          <input type="checkbox" checked={account.languages.includes(l.code)} onChange={e => setAccount({...account,
            languages: e.target.checked ? [...account.languages, l.code] : account.languages.filter((x: string) => x !== l.code)})}/>{l.flag} {l.code}</label>)}</div></Field>
        {!account.isNew && <>
          <Switch checked={account.active} disabled={account.id === data.you} onChange={active => setAccount({...account, active})} title={t('active')}/>
          {account.locked && <Switch checked={!!account.unlock} onChange={unlock => setAccount({...account, unlock})} title={t('unlock')}/>}
          {account.totp && <Switch checked={!!account.reset_totp} onChange={reset_totp => setAccount({...account, reset_totp})} title={t('resetTotp')}/>}
        </>}
      </div>
    </Modal>}

    {role && <Modal wide title={role.id ? role.name : t('newRole')} onClose={() => setRole(null)} footer={<>
      {role.id && !role.builtin && <button className="btn-ghost danger" onClick={() => confirmAction(t('confirmDelete')) &&
        run(() => api(`admin/team/roles/${role.id}`, 'DELETE').then(() => { setRole(null); refresh(); }))}>{t('delete')}</button>}
      {!role.builtin && <button className="btn-primary" disabled={!role.name.trim()} onClick={saveRole}>{t('save')}</button>}</>}>
      <fieldset disabled={role.builtin} className="stack">
        <Field label={t('name')}><input type="text" value={role.name} onChange={e => setRole({...role, name: e.target.value})}/></Field>
        <Field label={t('details')}><input type="text" value={role.description} onChange={e => setRole({...role, description: e.target.value})}/></Field>
        <b>{t('permissions')}</b>
        <div className="perm-grid">{data.permissions.map((p: any) => <label key={p.id} className="perm">
          <input type="checkbox" checked={role.permissions.includes(p.id)} onChange={e => setRole({...role,
            permissions: e.target.checked ? [...role.permissions, p.id] : role.permissions.filter((x: string) => x !== p.id)})}/>
          <span><code>{p.id}</code><small>{p.description}</small></span></label>)}</div>
      </fieldset>
    </Modal>}
  </div>;
}
