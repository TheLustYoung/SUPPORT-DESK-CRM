import {useState} from 'react';
import {api} from '../services/api';
import {LANGS, LANG_NAMES, useT, type Lang} from '../services/i18n';
import {Icon} from '../components/Icon';
import {LogoMark, Wordmark} from '../components/Logo';

const FEATURES = [
  {icon: 'tickets', title: 'loginF1', text: 'loginF1Text'},
  {icon: 'chat', title: 'loginF2', text: 'loginF2Text'},
  {icon: 'shield', title: 'loginF3', text: 'loginF3Text'},
];

export function Login({onLogin, setLang}: {onLogin: () => void; setLang: (lang: Lang) => void}) {
  const {t, lang} = useT();
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [code, setCode] = useState('');
  const [needCode, setNeedCode] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState(false), [caps, setCaps] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('auth/login', 'POST', {username, password, code});
      onLogin();
    } catch (err: any) {
      if (err.message === 'code_required') setNeedCode(true);
      setError(t(err.message));
    } finally {
      setBusy(false);
    }
  };

  return <main className="auth">
    <section className="auth-story" aria-labelledby="auth-headline">
      <Wordmark/>
      <div className="auth-copy">
        <span className="eyebrow">Support Desk</span>
        <h1 id="auth-headline">{t('loginHeadline')}</h1>
        <p>{t('loginLead')}</p>
      </div>
      <ul className="auth-features">{FEATURES.map(f => <li key={f.title}>
        <span className="auth-feature-icon"><Icon name={f.icon}/></span>
        <div><strong>{t(f.title)}</strong><small>{t(f.text)}</small></div>
      </li>)}</ul>
      <small className="auth-foot">© {new Date().getFullYear()} AfoninWebStudio · v1.0</small>
    </section>

    <section className="auth-panel">
      <div className="auth-top">
        <span className="auth-mobile-brand"><LogoMark size={32}/>Support Desk</span>
        <select value={lang} onChange={e => setLang(e.target.value as Lang)} aria-label="Language">
          {LANGS.map(code => <option key={code} value={code}>{LANG_NAMES[code]}</option>)}
        </select>
      </div>
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-card-head">
          <LogoMark size={44}/>
          <div><h2>{t('signIn')}</h2><p>{t('signInHint')}</p></div>
        </div>
        <label className="field">{t('username')}
          <span className="input-icon"><Icon name="user" size={16}/>
            <input type="text" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" autoFocus/></span>
        </label>
        <label className="field">{t('password')}
          <span className="input-icon"><Icon name="lock" size={16}/>
            <input type={reveal ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password"
              onKeyUp={e => setCaps(e.getModifierState('CapsLock'))}/>
            <button type="button" className="reveal" onClick={() => setReveal(v => !v)} aria-pressed={reveal}
              aria-label={t(reveal ? 'loginHide' : 'loginShow')} title={t(reveal ? 'loginHide' : 'loginShow')}>
              <Icon name={reveal ? 'eyeOff' : 'eye'} size={16}/></button>
          </span>
          {caps && <small className="caps"><Icon name="alert" size={14}/>{t('loginCaps')}</small>}
        </label>
        {needCode && <label className="field">{t('code')}
          <span className="input-icon"><Icon name="key" size={16}/>
            <input type="text" inputMode="numeric" value={code} onChange={e => setCode(e.target.value)} autoComplete="one-time-code" autoFocus/></span>
        </label>}
        {error && <div className="notice error" role="alert">{error}</div>}
        <button className="btn-primary auth-submit" disabled={busy || !username || !password}>
          {busy ? t('loading') : t('signIn')} {!busy && <Icon name="arrowRight" size={16}/>}
        </button>
        <p className="auth-note"><Icon name="shield" size={14}/>{t('loginSecure')}</p>
      </form>
    </section>
  </main>;
}
