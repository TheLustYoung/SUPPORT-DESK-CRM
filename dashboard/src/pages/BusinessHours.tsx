import {useEffect, useMemo, useState} from 'react';
import {api} from '../services/api';
import {useT} from '../services/i18n';

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
const PRIORITIES = ['urgent', 'high', 'normal', 'low'] as const;
const COLORS: Record<string, string> = {urgent: 'var(--bad)', high: 'var(--warn)', normal: 'var(--accent)', low: 'var(--faint)'};
type Hours = {enabled: boolean; timezone: string; days: Record<string, [string, string] | null>};
type Options = {business_hours: Hours; offline_auto_reply: boolean; sla_business_hours: boolean; sla_pause_pending_user: boolean};
type Target = {priority: string; first_response_minutes: number; resolution_minutes: number};

// Browsers leave «UTC» out of their list of zones: it comes first here, and the saved zone is always offered.
const ZONES: string[] = (() => {
  try { return ['UTC', ...(Intl as any).supportedValuesOf('timeZone').filter((z: string) => z !== 'UTC')]; } catch { return ['UTC', 'Europe/Moscow']; }
})();
const zonesWith = (current: string) => ZONES.includes(current) ? ZONES : [current, ...ZONES];

function Switch({checked, onChange, title, hint, disabled}: {checked: boolean; onChange: (v: boolean) => void; title: string; hint: string; disabled?: boolean}) {
  return <label className="switch" style={disabled ? {opacity: .5} : undefined}>
    <input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)}/>
    <span><b>{title}</b><small>{hint}</small></span>
  </label>;
}

/** Minutes as «1 ч 30 мин» next to the input. */
function Duration({minutes}: {minutes: number}) {
  const {t} = useT();
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return <small style={{color: 'var(--muted)', marginInlineStart: 8}}>{h ? `${h} ${t('hoursShort')} ` : ''}{m || !h ? `${m} ${t('minutes')}` : ''}</small>;
}

export function BusinessHours() {
  const {t} = useT();
  const [saved, setSaved] = useState<{options: Options; targets: Target[]} | null>(null);
  const [options, setOptions] = useState<Options | null>(null), [targets, setTargets] = useState<Target[]>([]);
  const [status, setStatus] = useState<{kind: 'ok' | 'error'; text: string} | null>(null), [busy, setBusy] = useState(false);

  const load = () => Promise.all([api('admin/settings/general'), api<Target[]>('admin/settings/sla')]).then(([general, sla]) => {
    const o: Options = {business_hours: general.business_hours, offline_auto_reply: general.offline_auto_reply,
      sla_business_hours: general.sla_business_hours, sla_pause_pending_user: general.sla_pause_pending_user};
    const rows = PRIORITIES.map(p => sla.find(r => r.priority === p) ?? {priority: p, first_response_minutes: 60, resolution_minutes: 1440});
    setSaved({options: o, targets: rows});
    setOptions(structuredClone(o));
    setTargets(structuredClone(rows));
  }).catch(err => setStatus({kind: 'error', text: t(err.message)}));
  useEffect(() => { load(); }, []);

  const dirty = useMemo(() => saved !== null && JSON.stringify({options, targets}) !== JSON.stringify(saved), [options, targets, saved]);
  if (!options) return status ? <div className="notice error">{status.text}</div> : <div className="card">{t('loading')}</div>;

  const hours = options.business_hours;
  const setHours = (change: Partial<Hours>) => setOptions({...options, business_hours: {...hours, ...change}});
  const setDay = (day: string, value: [string, string] | null) => setHours({days: {...hours.days, [day]: value}});
  const setTarget = (index: number, key: 'first_response_minutes' | 'resolution_minutes', value: number) =>
    setTargets(targets.map((row, i) => i === index ? {...row, [key]: Math.max(1, value || 1)} : row));

  const save = async () => {
    setBusy(true);
    setStatus(null);
    try {
      await api('admin/settings/general', 'PATCH', options);
      await api('admin/settings/sla', 'PUT', targets);
      await load();
      setStatus({kind: 'ok', text: t('saved')});
    } catch (err: any) {
      setStatus({kind: 'error', text: t(err.message)});
    } finally {
      setBusy(false);
    }
  };

  return <>
    <div className="grid two">
      <section className="card">
        <h2>{t('hoursTitle')}</h2>
        <p className="hint">{t('hoursText')}</p>
        <Switch checked={hours.enabled} onChange={v => setHours({enabled: v})} title={t('hoursEnabled')} hint={t('hoursEnabledHint')}/>
        <label className="field" style={{margin: '8px 0 14px'}}>{t('timezone')}
          <select value={hours.timezone} onChange={e => setHours({timezone: e.target.value})} disabled={!hours.enabled}>
            {zonesWith(hours.timezone).map(zone => <option key={zone} value={zone}>{zone}</option>)}
          </select>
        </label>
        <div className="days" style={hours.enabled ? undefined : {opacity: .5, pointerEvents: 'none'}}>
          {DAYS.map(day => {
            const span = hours.days[day];
            return <div className="day" key={day}>
              <b>{t(day)}</b>
              <label className="switch" style={{padding: 0}}>
                <input type="checkbox" checked={!!span} onChange={e => setDay(day, e.target.checked ? ['09:00', '18:00'] : null)} aria-label={t(day)}/>
              </label>
              {span ? <span className="times">
                {t('worksFrom')}<input type="time" value={span[0]} onChange={e => setDay(day, [e.target.value, span[1]])}/>
                {t('worksTo')}<input type="time" value={span[1]} onChange={e => setDay(day, [span[0], e.target.value])}/>
                {span[1] <= span[0] && <small>{t('overnight')}</small>}
              </span> : <span className="times off">{t('dayOff')}</span>}
            </div>;
          })}
        </div>
        <Switch checked={options.offline_auto_reply} onChange={v => setOptions({...options, offline_auto_reply: v})}
                title={t('offlineReply')} hint={t('offlineReplyHint')} disabled={!hours.enabled}/>
      </section>

      <div className="grid" style={{alignContent: 'start'}}>
        <section className="card">
          <h2>{t('slaTitle')}</h2>
          <p className="hint">{t('slaText')}</p>
          <Switch checked={options.sla_business_hours} onChange={v => setOptions({...options, sla_business_hours: v})}
                  title={t('slaBusiness')} hint={t('slaBusinessHint')} disabled={!hours.enabled}/>
          <Switch checked={options.sla_pause_pending_user} onChange={v => setOptions({...options, sla_pause_pending_user: v})}
                  title={t('slaPause')} hint={t('slaPauseHint')}/>
        </section>
        <section className="card">
          <h2>{t('targetsTitle')}</h2>
          <p className="hint">{t('targetsText')}</p>
          <table className="plain">
            <thead><tr><th>{t('priority')}</th><th>{t('firstResponse')}, {t('minutes')}</th><th>{t('resolution')}, {t('minutes')}</th></tr></thead>
            <tbody>{targets.map((row, index) => <tr key={row.priority}>
              <td><span className="dot" style={{background: COLORS[row.priority]}}/>{t(row.priority)}</td>
              <td><input type="number" min={1} value={row.first_response_minutes} onChange={e => setTarget(index, 'first_response_minutes', +e.target.value)}/>
                <Duration minutes={row.first_response_minutes}/></td>
              <td><input type="number" min={1} value={row.resolution_minutes} onChange={e => setTarget(index, 'resolution_minutes', +e.target.value)}/>
                <Duration minutes={row.resolution_minutes}/></td>
            </tr>)}</tbody>
          </table>
          <p className="hint" style={{margin: '12px 0 0'}}>{t('slaRecalc')}</p>
        </section>
      </div>
    </div>
    <div className="savebar">
      <span>{status ? <span className={'notice ' + status.kind}>{status.text}</span> : dirty ? t('unsaved') : ''}</span>
      <button className="btn-ghost" disabled={!dirty || busy} onClick={() => saved && (setOptions(structuredClone(saved.options)), setTargets(structuredClone(saved.targets)))}>{t('reset')}</button>
      <button className="btn-primary" disabled={!dirty || busy} onClick={save}>{t('save')}</button>
    </div>
  </>;
}
