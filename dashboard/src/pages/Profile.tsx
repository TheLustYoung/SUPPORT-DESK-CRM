import {useState} from 'react';
import {api} from '../services/api';
import {useT} from '../services/i18n';
import {Card, Field, Switch, useLookups, useNotice} from '../components/ui';
import {QrCode} from '../components/QrCode';

export function Profile({me, onChanged, onSignedOut}: {me: any; onChanged: () => void; onSignedOut: () => void}) {
  const {t} = useT();
  const {lookups} = useLookups();
  const [form, setForm] = useState({display_name: me.display_name, email: me.email, languages: me.languages ?? [], notify: {telegram: true, sound: true, ...me.notify}});
  const [passwords, setPasswords] = useState({current: '', new: ''});
  const [totp, setTotp] = useState<{secret: string; uri: string} | null>(null), [code, setCode] = useState('');
  const [link, setLink] = useState<any>(null);
  const {run, view} = useNotice();
  return <div className="grid two">
    <Card title={t('profile')}>
      <div className="stack">
        <Field label={t('displayName')}><input type="text" value={form.display_name} onChange={e => setForm({...form, display_name: e.target.value})}/></Field>
        <Field label={t('email')}><input type="text" value={form.email} onChange={e => setForm({...form, email: e.target.value})}/></Field>
        <Field label={t('speaks')}><div className="chips">{lookups.languages.map(l => <label key={l.code} className="chip check">
          <input type="checkbox" checked={form.languages.includes(l.code)} onChange={e => setForm({...form,
            languages: e.target.checked ? [...form.languages, l.code] : form.languages.filter((x: string) => x !== l.code)})}/>{l.flag} {l.code}</label>)}</div></Field>
        <Switch checked={form.notify.sound} onChange={sound => setForm({...form, notify: {...form.notify, sound}})} title={t('notifySound')}/>
        <Switch checked={form.notify.telegram} onChange={telegram => setForm({...form, notify: {...form.notify, telegram}})} title={t('notifyTelegram')}/>
        <button className="btn-primary" onClick={() => run(() => api('admin/me', 'PATCH', form).then(onChanged))}>{t('save')}</button>
        {view}
      </div>
    </Card>
    <div className="grid" style={{alignContent: 'start'}}>
      <Card title={t('twoFactorTitle')} hint={me.totp ? t('twoFactorOn') : t('twoFactorOff')}>
        {!me.totp && !totp && <button className="btn-primary" onClick={() => run(() => api('admin/me/totp', 'POST').then(setTotp), ' ')}>{t('enable2fa')}</button>}
        {totp && <div className="stack"><p>{t('scanQr')}</p><QrCode text={totp.uri}/><code className="secret">{totp.secret}</code>
          <input type="text" inputMode="numeric" placeholder="123456" value={code} onChange={e => setCode(e.target.value)}/>
          <button className="btn-primary" onClick={() => run(() => api('admin/me/totp/confirm', 'POST', {code}).then(() => { setTotp(null); setCode(''); onChanged(); }))}>{t('confirm')}</button></div>}
        {me.totp && <div className="inline-form"><input type="text" inputMode="numeric" placeholder="123456" value={code} onChange={e => setCode(e.target.value)}/>
          <button className="btn-ghost danger" onClick={() => run(() => api('admin/me/totp/disable', 'POST', {code}).then(() => { setCode(''); onChanged(); }))}>{t('disable2fa')}</button></div>}
      </Card>
      <Card title={t('telegramTitle')} hint={t('telegramText')}>
        {me.telegram ? <><p>✅ {t('telegramLinked')}</p><button className="btn-ghost" onClick={() => run(() => api('admin/me/telegram-unlink', 'POST').then(onChanged))}>{t('unlink')}</button></>
          : <><button className="btn-primary" onClick={() => run(() => api('admin/me/telegram-code', 'POST').then(setLink), ' ')}>{t('getCode')}</button>
            {link && <p>{t('sendToBot')} <code className="secret">/link {link.code}</code>{link.bot_username && <> → <a href={`https://t.me/${link.bot_username}`} target="_blank" rel="noreferrer">@{link.bot_username}</a></>}</p>}</>}
      </Card>
      <Card title={t('changePassword')}>
        <div className="stack">
          <Field label={t('currentPassword')}><input type="password" autoComplete="current-password" value={passwords.current} onChange={e => setPasswords({...passwords, current: e.target.value})}/></Field>
          <Field label={t('newPassword')}><input type="password" autoComplete="new-password" placeholder={t('passwordRule')} value={passwords.new} onChange={e => setPasswords({...passwords, new: e.target.value})}/></Field>
          <button className="btn-primary" disabled={!passwords.current || !passwords.new}
                  onClick={() => run(() => api('admin/me/password', 'POST', passwords).then(() => { alert(t('passwordChanged')); onSignedOut(); }))}>{t('changePassword')}</button>
        </div>
      </Card>
    </div>
  </div>;
}
