import {Component, type ReactNode, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {api, query, setUnauthorizedHandler} from './services/api';
import {I18n, LANGS, LANG_NAMES, makeT, rtl, useT, type Lang} from './services/i18n';
import {Icon} from './components/Icon';
import {Brand} from './components/Logo';
import {AccessContext, LookupsContext, type Lookups, useFormat} from './components/ui';
import {Login} from './pages/Login';
import {Overview} from './pages/Overview';
import {Tickets} from './pages/Tickets';
import {Users} from './pages/Users';
import {Campaigns} from './pages/Campaigns';
import {Faq} from './pages/Faq';
import {Content} from './pages/Content';
import {Analytics} from './pages/Analytics';
import {BusinessHours} from './pages/BusinessHours';
import {Settings} from './pages/Settings';
import {Team} from './pages/Team';
import {Audit} from './pages/Audit';
import {System} from './pages/System';
import {Profile} from './pages/Profile';

type Page = 'overview' | 'tickets' | 'users' | 'campaigns' | 'faq' | 'content' | 'analytics' | 'hours' | 'settings' | 'team' | 'audit' | 'system' | 'profile';
// The permission a page needs (the API checks it again on every call).
const PAGES: [Page, string | string[] | null][] = [
  ['overview', null], ['tickets', 'tickets.view'], ['users', 'users.view'], ['campaigns', ['campaigns.edit', 'campaigns.send']],
  ['faq', ['faq.edit', 'faq.publish']], ['content', ['content.edit', 'templates.edit']], ['analytics', 'analytics.view'], ['hours', 'settings.edit'],
  ['settings', 'settings.edit'], ['team', 'team.manage'], ['audit', 'audit.view'], ['system', 'system.manage'],
];
const EMPTY_LOOKUPS: Lookups = {departments: [], categories: [], statuses: [], priorities: [], languages: [], agents: [], templates: []};

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try { const v = localStorage.getItem(key); if (v && (allowed as readonly string[]).includes(v)) return v as T; } catch {}
  return fallback;
}
function remember(key: string, value: string) { try { localStorage.setItem(key, value); } catch {} }

/** A short chime for new notifications (no audio file needed). */
function chime() {
  try {
    const ctx = new AudioContext(), osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.frequency.value = 880; gain.gain.setValueAtTime(.12, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(.001, ctx.currentTime + .5);
    osc.connect(gain).connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + .5);
  } catch {}
}

export function App() {
  const [lang, setLang] = useState<Lang>(() => stored('language', LANGS, 'ru'));
  const [theme, setTheme] = useState<'dark' | 'light'>(() => stored('theme', ['dark', 'light'] as const, 'light'));
  const i18n = useMemo(() => ({lang, t: makeT(lang)}), [lang]);
  const {t} = i18n;
  const [me, setMe] = useState<any>(null), [boot, setBoot] = useState(true);
  // A link from a Telegram notification (/?ticket=42) opens that ticket; the address is cleaned right away.
  const [linkedTicket] = useState(() => Number(new URLSearchParams(window.location.search).get('ticket')) || null);
  const [page, setPage] = useState<Page>(() => linkedTicket ? 'tickets' : stored('page', [...PAGES.map(p => p[0]), 'profile'] as Page[], 'overview'));
  const [ticketId, setTicketId] = useState<number | null>(linkedTicket), [userId, setUserId] = useState<number | null>(null);
  useEffect(() => { if (linkedTicket) window.history.replaceState(null, '', window.location.pathname); }, [linkedTicket]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [lookups, setLookups] = useState<Lookups>(EMPTY_LOOKUPS);

  const loadMe = () => api('admin/me').then(setMe);
  const loadLookups = useCallback(() => { api<Lookups>('admin/lookups').then(setLookups).catch(() => {}); }, []);
  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    loadMe().catch(() => {}).finally(() => setBoot(false));
  }, []);
  useEffect(() => { if (me) loadLookups(); }, [me?.id]);
  useEffect(() => { document.documentElement.lang = lang; document.documentElement.dir = rtl(lang) ? 'rtl' : 'ltr'; remember('language', lang); }, [lang]);
  useEffect(() => { document.documentElement.dataset.theme = theme; remember('theme', theme); }, [theme]);

  const can = useCallback((need: string | string[] | null) => !need || (!!me && (Array.isArray(need) ? need : [need]).some(p => me.permissions.includes(p))), [me]);
  const visible = PAGES.filter(([, need]) => can(need));
  useEffect(() => { if (me && page !== 'profile' && !visible.some(([p]) => p === page)) setPage('overview'); }, [me]);
  const go = (p: Page | string) => { setPage(p as Page); remember('page', p); setMenuOpen(false); setTicketId(null); setUserId(null); window.scrollTo({top: 0}); };
  const openTicket = (id: number) => { setPage('tickets'); setTicketId(id); setMenuOpen(false); window.scrollTo({top: 0}); };
  const openUser = (id: number) => { setPage('users'); setUserId(id); window.scrollTo({top: 0}); };
  const signOut = () => api('auth/logout', 'POST').finally(() => setMe(null));

  let content;
  if (boot) content = <div className="boot">{t('loading')}</div>;
  else if (!me) content = <Login onLogin={() => loadMe()} setLang={setLang}/>;
  else {
    const name = me.display_name || me.username;
    content = <div className={'shell' + (menuOpen ? ' menu-open' : '')} onClick={e => { if (menuOpen && e.target === e.currentTarget) setMenuOpen(false); }}>
      <aside className="sidebar">
        <Brand product={me.company || t('appName')}/>
        <nav className="nav">
          {visible.map(([p]) => <button key={p} className={p === page ? 'active' : ''} onClick={() => go(p)} title={t(p)}><Icon name={p}/><span>{t(p)}</span></button>)}
        </nav>
        <div className="sidebar-foot nav">
          <select value={lang} onChange={e => setLang(e.target.value as Lang)} aria-label="Language">
            {LANGS.map(code => <option key={code} value={code}>{LANG_NAMES[code]}</option>)}
          </select>
          <button className={page === 'profile' ? 'active' : ''} onClick={() => go('profile')} title={t('profile')}><Icon name="team"/><span>{t('profile')}</span></button>
          <button onClick={signOut} title={t('signOut')}><Icon name="logout"/><span>{t('signOut')}</span></button>
        </div>
      </aside>
      <main className="main-frame">
        <header className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setMenuOpen(true)} aria-label={t('openMenu')}><Icon name="menu"/></button>
          <div className="title"><h1>{page === 'overview' ? <>{t('welcome')} <span>{name}</span></> : t(page)}</h1><p title={t('hint_' + page)}>{t('hint_' + page)}</p></div>
          {can(['tickets.view', 'users.view']) && <GlobalSearch openTicket={openTicket} openUser={openUser}/>}
          <Bell me={me} openTicket={openTicket}/>
          <button className="icon-btn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={t('toggleTheme')}>
            <Icon name={theme === 'dark' ? 'sun' : 'moon'}/></button>
          <button className="profile" onClick={() => go('profile')}><span className="avatar">{name[0]?.toUpperCase()}</span><div>{name}<small>{me.role_name}</small></div></button>
        </header>
        <PageGuard key={`${page}:${ticketId ?? ''}:${userId ?? ''}`} message={t('failed')} retry={t('refresh')}>
        {page === 'overview' && <Overview openTicket={openTicket} go={go}/>}
        {page === 'tickets' && <Tickets openId={ticketId} setOpenId={setTicketId} openUser={openUser}/>}
        {page === 'users' && <Users openId={userId} setOpenId={setUserId} openTicket={openTicket}/>}
        {page === 'campaigns' && <Campaigns/>}
        {page === 'faq' && <Faq/>}
        {page === 'content' && <Content/>}
        {page === 'analytics' && <Analytics/>}
        {page === 'hours' && <BusinessHours/>}
        {page === 'settings' && <Settings/>}
        {page === 'team' && <Team/>}
        {page === 'audit' && <Audit/>}
        {page === 'system' && <System/>}
        {page === 'profile' && <Profile me={me} onChanged={loadMe} onSignedOut={() => setMe(null)}/>}
        </PageGuard>
      </main>
    </div>;
  }
  return <I18n.Provider value={i18n}><AccessContext.Provider value={{can, me}}>
    <LookupsContext.Provider value={{lookups, reload: loadLookups}}>{content}</LookupsContext.Provider>
  </AccessContext.Provider></I18n.Provider>;
}

/** The bell: staff notifications (new tickets, replies, assignments, overdue), with a sound for new ones. */
/** A failing page shows a message instead of blanking the whole dashboard; navigation (a new key) starts it afresh. */
class PageGuard extends Component<{message: string; retry: string; children: ReactNode}, {failed: boolean}> {
  state = {failed: false};
  static getDerivedStateFromError() { return {failed: true}; }
  componentDidCatch(error: unknown) { console.error(error); }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="card placeholder"><div><b>{this.props.message}</b>
      <button className="btn-ghost" onClick={() => this.setState({failed: false})}>{this.props.retry}</button></div></div>;
  }
}

/** A notification in the dashboard's language: notif_<kind> with its values; the stored English text for old ones. */
function notificationText(t: (key: string) => string, n: {kind: string; text: string; data?: Record<string, unknown>}): string {
  const template = n.data && Object.keys(n.data).length ? t('notif_' + n.kind) : '';
  if (!template || template === 'notif_' + n.kind) return n.text;
  const what: Record<string, string> = {first_response: t('firstResponse').toLocaleLowerCase(), resolution: t('resolution').toLocaleLowerCase()};
  return template.replace(/{(\w+)}/g, (_, key: string) => {
    const value = n.data?.[key];
    return key === 'what' ? what[String(value)] ?? String(value ?? '') : String(value ?? '');
  });
}

function Bell({me, openTicket}: {me: any; openTicket: (id: number) => void}) {
  const {t} = useT();
  const {date} = useFormat();
  const [data, setData] = useState<any>({items: [], unread: 0}), [open, setOpen] = useState(false);
  const latest = useRef<number | null>(null);
  const load = useCallback(() => api('admin/notifications').then(d => {
    if (latest.current !== null && d.latest_unread && d.latest_unread > latest.current && me.notify?.sound !== false) chime();
    latest.current = d.latest_unread ?? latest.current ?? 0;
    setData(d);
  }).catch(() => {}), [me]);
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, [load]);
  const readAll = () => api('admin/notifications/read', 'POST', {ids: []}).then(load);
  return <div className="bell">
    <button className="icon-btn" onClick={() => setOpen(!open)} aria-label={t('notifications')}><Icon name="bell"/>
      {data.unread > 0 && <span className="bell-count">{data.unread > 99 ? '99+' : data.unread}</span>}</button>
    {open && <div className="dropdown">
      <div className="dropdown-head"><b>{t('notifications')}</b><button className="btn-ghost small" onClick={readAll}>{t('markAllRead')}</button></div>
      {data.items.length ? data.items.map((n: any) => <button key={n.id} className={'dropdown-row' + (n.read ? '' : ' unread')} onClick={() => {
        api('admin/notifications/read', 'POST', {ids: [n.id]}).then(load);
        if (n.ticket_id) openTicket(n.ticket_id);
        setOpen(false);
      }}><span dir="auto">{notificationText(t, n)}</span><small>{date(n.created_at)}</small></button>) : <div className="empty">{t('noNotifications')}</div>}
    </div>}
  </div>;
}

function GlobalSearch({openTicket, openUser}: {openTicket: (id: number) => void; openUser: (id: number) => void}) {
  const {t} = useT();
  const [q, setQ] = useState(''), [result, setResult] = useState<any>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setResult(null); return; }
    const timer = setTimeout(() => api(`admin/search?${query({q})}`).then(setResult).catch(() => {}), 300);
    return () => clearTimeout(timer);
  }, [q]);
  const close = () => { setQ(''); setResult(null); };
  return <div className="search">
    <input type="search" placeholder={t('searchPlaceholder')} value={q} onChange={e => setQ(e.target.value)}/>
    {result && <div className="dropdown">
      {result.tickets.map((x: any) => <button key={'t' + x.id} className="dropdown-row" onClick={() => { openTicket(x.id); close(); }}>
        <span><b>{x.number}</b> {x.subject}</span><small>{x.user?.name}</small></button>)}
      {result.users.map((u: any) => <button key={'u' + u.id} className="dropdown-row" onClick={() => { openUser(u.id); close(); }}>
        <span>👤 {u.name}</span><small>{u.username ? '@' + u.username : u.id}</small></button>)}
      {!result.tickets.length && !result.users.length && <div className="empty">{t('nothing')}</div>}
    </div>}
  </div>;
}
