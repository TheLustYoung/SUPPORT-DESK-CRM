import {Fragment} from 'react';
import {api, download} from '../services/api';
import {useT} from '../services/i18n';
import {Badge, Card, Empty, useFormat, useNotice, useResource} from '../components/ui';

export function System() {
  const {t} = useT();
  const {date, size} = useFormat();
  const {data, reload} = useResource<any>('admin/system');
  const errors = useResource<any[]>('admin/system/errors');
  const {run, view} = useNotice();
  if (!data) return <div className="card">{t('loading')}</div>;
  const check = data.backups.restore_check ?? {};
  return <div className="grid">
    <div className="grid three">
      <Card title={t('bot')}>
        <p><Badge color={data.bot.online ? '#5ad19a' : '#ff6b81'}>{data.bot.online ? t('online') : t('offline')}</Badge> <b>@{data.bot.username || '—'}</b></p>
        <dl className="facts"><dt>{t('mode')}</dt><dd>{data.bot.mode || '—'}</dd><dt>{t('lastSeenBot')}</dt><dd>{date(data.bot.last_seen)}</dd>
          <dt>{t('environment')}</dt><dd>{data.environment}</dd></dl>
      </Card>
      <Card title={t('checks')}>
        <dl className="facts">{Object.entries(data.checks).map(([k, v]) => <Fragment key={k}><dt>{t('check_' + k)}</dt><dd><Badge color={v === 'ok' ? '#5ad19a' : '#ff6b81'}>{t('state_' + v)}</Badge></dd></Fragment>)}
          <dt>{t('schedulerLastRun')}</dt><dd>{date(data.scheduler_seen)}</dd></dl>
        {data.checks.scheduler !== 'ok' && <p className="notice error">{t('schedulerStopped')}</p>}
      </Card>
      <Card title={t('queue')} actions={<button className="btn-ghost small" onClick={() => run(() => api('admin/system/reconcile', 'POST').then(reload))}>{t('restartQueue')}</button>}>
        <dl className="facts">{Object.entries(data.queue.messages).map(([k, v]) => <Fragment key={k}><dt>{t('delivery_' + k)}</dt><dd>{String(v)}</dd></Fragment>)}
          {Object.entries(data.queue.campaign_deliveries).map(([k, v]) => <Fragment key={'c' + k}><dt>{t('campaigns')}: {t('d_' + k)}</dt><dd>{String(v)}</dd></Fragment>)}
          <dt>{t('oldestQueued')}</dt><dd>{date(data.queue.oldest_queued)}</dd></dl>
      </Card>
    </div>
    <Card title={t('backups')} actions={<><button className="btn-primary small" onClick={() => run(() => api('admin/system/backups', 'POST').then(reload), t('backupRequested'))}>{t('backupNow')}</button>{view}</>}>
      <p>{t('restoreCheck')}: {check.status ? <Badge color={check.status === 'ok' ? '#5ad19a' : '#ff6b81'}>{check.status}</Badge> : '—'} {check.checked_at && date(check.checked_at)}
        {check.backup && <small className="muted"> · {check.backup} · {t('restoreCounts', {tables: check.tables, tickets: check.tickets})}</small>}</p>
      {data.backups.items.length ? <div className="list">{data.backups.items.map((b: any) => <div key={b.name} className="list-row static">
        <span><b>{b.name}</b><small>{size(b.size)} · {date(new Date(b.created_at * 1000).toISOString())}</small></span>
        <button className="btn-ghost small" onClick={() => run(() => download(`admin/system/backups/${b.name}`), t('download'))}>{t('download')}</button>
      </div>)}</div> : <Empty>{t('noBackups')}</Empty>}
    </Card>
    <Card title={t('errors')} actions={<button className="btn-ghost small" onClick={() => run(() => api('admin/system/errors/clear', 'POST').then(() => errors.reload()))}>{t('clearErrors')}</button>}>
      {errors.data?.length ? <div className="table-wrap"><table className="table compact"><thead><tr><th>{t('date')}</th><th>{t('source')}</th><th>{t('action')}</th><th>{t('details')}</th></tr></thead>
        <tbody>{errors.data.map(e => <tr key={e.id}><td>{date(e.created_at)}</td><td>{e.source}</td><td><code>{e.kind}</code></td><td className="pre small">{e.message}</td></tr>)}</tbody></table></div> : <Empty/>}
    </Card>
  </div>;
}
