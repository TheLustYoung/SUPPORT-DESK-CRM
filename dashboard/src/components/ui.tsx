import {createContext, useCallback, useContext, useEffect, useState, type ReactNode} from 'react';
import {api, download} from '../services/api';
import {useT} from '../services/i18n';

// ---------------------------------------------------------------- data loading
export function useResource<T = any>(route: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const reload = useCallback(() => {
    if (!route) return Promise.resolve();
    setLoading(true);
    return api<T>(route).then(d => { setData(d); setError(''); }).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [route]);
  useEffect(() => { reload(); }, [reload, ...deps]);
  return {data, error, loading, reload, setData};
}

/** Departments, categories, statuses, languages, agents and templates: loaded once for every page. */
export type Lookups = {departments: any[]; categories: any[]; statuses: any[]; priorities: string[]; languages: any[]; agents: any[]; templates: any[]};
export const LookupsContext = createContext<{lookups: Lookups; reload: () => void}>({
  lookups: {departments: [], categories: [], statuses: [], priorities: [], languages: [], agents: [], templates: []}, reload: () => {}});
export const useLookups = () => useContext(LookupsContext);

/** The permissions of the signed-in staff member. */
export const AccessContext = createContext<{can: (p: string | string[]) => boolean; me: any}>({can: () => false, me: null});
export const useAccess = () => useContext(AccessContext);

/** A translated value {language: text}: the dashboard language, the default language, English, any. */
export function useName() {
  const {lang} = useT();
  const {lookups} = useLookups();
  const fallback = lookups.languages.find(l => l.is_default)?.code ?? 'en';
  return (values: Record<string, string> | null | undefined) =>
    !values ? '' : values[lang] || values[fallback] || values.en || Object.values(values).find(Boolean) || '';
}

// ---------------------------------------------------------------- formatting
export function useFormat() {
  const {t, lang} = useT();
  const date = (value?: string | null, withTime = true) => value ? new Date(value).toLocaleString(lang, withTime
    ? {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'} : {day: '2-digit', month: '2-digit', year: 'numeric'}) : '—';
  const duration = (seconds?: number | null) => {
    if (seconds === null || seconds === undefined) return '—';
    const m = Math.round(seconds / 60);
    if (m < 60) return `${m} ${t('minutes')}`;
    const h = Math.floor(m / 60);
    if (h < 48) return `${h} ${t('hoursShort')} ${m % 60 ? `${m % 60} ${t('minutes')}` : ''}`.trim();
    return `${Math.floor(h / 24)} ${t('daysShort')} ${h % 24 ? `${h % 24} ${t('hoursShort')}` : ''}`.trim();
  };
  const size = (bytes?: number | null) => !bytes ? '—' : bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return {date, duration, size};
}

// ---------------------------------------------------------------- building blocks
export function Card({title, hint, actions, children, className = ''}: {title?: ReactNode; hint?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string}) {
  return <section className={'card ' + className}>
    {(title || actions) && <div className="card-head"><div>{title && <h2>{title}</h2>}{hint && <p className="hint">{hint}</p>}</div>{actions && <div className="actions">{actions}</div>}</div>}
    {children}
  </section>;
}

export function Pills<T extends string>({value, options, onChange}: {value: T; options: [T, string][]; onChange: (v: T) => void}) {
  return <div className="pills">{options.map(([v, label]) =>
    <button key={v} className={'pill' + (v === value ? ' active' : '')} onClick={() => onChange(v)} title={label}>{label}</button>)}</div>;
}

export function Switch({checked, onChange, title, hint, disabled}: {checked: boolean; onChange: (v: boolean) => void; title?: string; hint?: string; disabled?: boolean}) {
  return <label className="switch" style={disabled ? {opacity: .5} : undefined}>
    <input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} aria-label={title}/>
    {(title || hint) && <span>{title && <b>{title}</b>}{hint && <small>{hint}</small>}</span>}
  </label>;
}

export function Field({label, children, wide}: {label: ReactNode; children: ReactNode; wide?: boolean}) {
  return <label className={'field' + (wide ? ' wide' : '')}>{label}{children}</label>;
}

export function Modal({title, onClose, children, footer, wide}: {title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean}) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [onClose]);
  return <div className="modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div className={'modal' + (wide ? ' wide' : '')} role="dialog" aria-modal="true">
      <div className="modal-head"><h2>{title}</h2><button className="icon-btn small" onClick={onClose} aria-label="Close">✕</button></div>
      <div className="modal-body">{children}</div>
      {footer && <div className="modal-foot">{footer}</div>}
    </div>
  </div>;
}

export function Badge({color, children}: {color?: string; children: ReactNode}) {
  return <span className="badge" style={color ? {color, borderColor: color + '66', background: color + '1f'} : undefined}>{children}</span>;
}

export function StatusBadge({status}: {status: string}) {
  const {lookups} = useLookups();
  const name = useName();
  const row = lookups.statuses.find(s => s.key === status);
  return <Badge color={row?.color}>{row ? name(row.name) : status}</Badge>;
}

/** Priority as a filled chip (coral = urgent/high, lavender = normal, lime = low), styled in styles/pages/_desk.scss. */
export function PriorityBadge({priority}: {priority: string}) {
  const {t} = useT();
  return <span className={'prio prio-' + priority}>{t(priority)}</span>;
}

export function Pager({page, size, total, onPage}: {page: number; size: number; total: number; onPage: (p: number) => void}) {
  const {t} = useT();
  const pages = Math.max(1, Math.ceil(total / size));
  if (pages <= 1) return null;
  return <div className="pager">
    <button className="btn-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>{t('prev')}</button>
    <span>{page} / {pages}</span>
    <button className="btn-ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>{t('next')}</button>
  </div>;
}

export function Empty({children}: {children?: ReactNode}) {
  const {t} = useT();
  return <div className="empty">{children ?? t('nothing')}</div>;
}

/** Status line for actions: «Saved» or a translated error. */
export function useNotice() {
  const {t} = useT();
  const [notice, setNotice] = useState<{kind: 'ok' | 'error'; text: string} | null>(null);
  useEffect(() => { if (notice?.kind === 'ok') { const timer = setTimeout(() => setNotice(null), 3000); return () => clearTimeout(timer); } }, [notice]);
  const run = async <T,>(action: () => Promise<T>, success?: string): Promise<T | undefined> => {
    try {
      const result = await action();
      setNotice({kind: 'ok', text: success ?? t('saved')});
      return result;
    } catch (error: any) {
      setNotice({kind: 'error', text: t(error.message)});
      return undefined;
    }
  };
  const view = notice ? <div className={'notice ' + notice.kind}>{notice.text}</div> : null;
  return {run, view, setNotice};
}

/** Text in several languages: a tab per enabled language, a mark on the ones still missing. */
export function LangText({value, onChange, multiline, rows = 4, placeholder, maxLength}: {
  value: Record<string, string>; onChange: (v: Record<string, string>) => void; multiline?: boolean; rows?: number; placeholder?: string; maxLength?: number}) {
  const {lookups} = useLookups();
  const {t} = useT();
  const languages = lookups.languages.filter(l => l.enabled);
  const [current, setCurrent] = useState<string>(() => languages.find(l => l.is_default)?.code ?? languages[0]?.code ?? 'en');
  const lang = languages.find(l => l.code === current) ?? languages[0];
  const text = value?.[current] ?? '';
  const set = (v: string) => onChange({...value, [current]: v});
  return <div className="langtext">
    <div className="langtabs">{languages.map(l => <button key={l.code} type="button" className={l.code === current ? 'active' : ''}
      onClick={() => setCurrent(l.code)} title={value?.[l.code] ? '' : t('missingTranslation')}>
      {l.flag} {l.code}{!value?.[l.code] && <i className="missing-dot"/>}</button>)}</div>
    {multiline ? <textarea rows={rows} dir={lang?.rtl ? 'rtl' : 'auto'} value={text} placeholder={placeholder} maxLength={maxLength} onChange={e => set(e.target.value)}/>
      : <input type="text" dir={lang?.rtl ? 'rtl' : 'auto'} value={text} placeholder={placeholder} maxLength={maxLength} onChange={e => set(e.target.value)}/>}
  </div>;
}

export function ExportButtons({route, params}: {route: string; params: string}) {
  const {t} = useT();
  const {run, view} = useNotice();
  return <div className="actions">
    {(['csv', 'xlsx', 'pdf'] as const).map(format => <button key={format} className="btn-ghost small"
      onClick={() => run(() => download(`${route}/export?format=${format}&${params}`), t('download'))}>{format.toUpperCase()}</button>)}
    {view}
  </div>;
}

/** A tiny area chart of daily values (no chart library). */
export function AreaChart({points, series}: {points: Record<string, any>[]; series: {key: string; label: string; color: string}[]}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!points.length) return <Empty/>;
  const width = 720, height = 220, pad = 28;
  const max = Math.max(1, ...points.flatMap(p => series.map(s => Number(p[s.key]) || 0)));
  const x = (i: number) => pad + (points.length === 1 ? (width - 2 * pad) / 2 : i * (width - 2 * pad) / (points.length - 1));
  const y = (v: number) => height - pad - (v / max) * (height - 2 * pad);
  const path = (key: string) => points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(Number(p[key]) || 0)}`).join(' ');
  const step = Math.max(1, Math.ceil(points.length / 8));
  return <div className="chart">
    <svg viewBox={`0 0 ${width} ${height}`} onMouseLeave={() => setHover(null)}>
      <defs>{series.map(s => <linearGradient key={s.key} id={'g-' + s.key} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor={s.color} stopOpacity=".45"/><stop offset="1" stopColor={s.color} stopOpacity="0"/></linearGradient>)}</defs>
      {[0, .5, 1].map(f => <line key={f} x1={pad} x2={width - pad} y1={y(max * f)} y2={y(max * f)} className="grid-line"/>)}
      {[0, .5, 1].map(f => <text key={f} x={4} y={y(max * f) + 4} className="axis">{Math.round(max * f)}</text>)}
      {series.map(s => <g key={s.key}>
        <path d={`${path(s.key)} L${x(points.length - 1)},${height - pad} L${x(0)},${height - pad} Z`} fill={`url(#g-${s.key})`}/>
        <path d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2"/></g>)}
      {points.map((p, i) => <g key={i}>
        {i % step === 0 && <text x={x(i)} y={height - 6} textAnchor="middle" className="axis">{String(p.day).slice(5)}</text>}
        <rect x={x(i) - 8} y={pad} width={16} height={height - 2 * pad} fill="transparent" onMouseEnter={() => setHover(i)}/>
      </g>)}
      {hover !== null && <g><line x1={x(hover)} x2={x(hover)} y1={pad} y2={height - pad} className="hover-line"/>
        {series.map(s => <circle key={s.key} cx={x(hover)} cy={y(Number(points[hover][s.key]) || 0)} r="5" fill={s.color}/>)}</g>}
    </svg>
    <div className="legend">{series.map(s => <span key={s.key}><i style={{background: s.color}}/>{s.label}</span>)}
      {hover !== null && <b>{points[hover].day}: {series.map(s => `${s.label} ${points[hover][s.key]}`).join(' · ')}</b>}</div>
  </div>;
}

export function confirmAction(text: string) { return window.confirm(text); }
