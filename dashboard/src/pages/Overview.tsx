import {useState} from 'react';
import {query} from '../services/api';
import {useT} from '../services/i18n';
import {AreaChart, Card, Empty, Pills, PriorityBadge, StatusBadge, useAccess, useFormat, useResource} from '../components/ui';

type Period = '7' | '30' | '182' | '365';

export function Overview({openTicket, go}: {openTicket: (id: number) => void; go: (page: string) => void}) {
  const {t} = useT();
  const {can} = useAccess();
  const {duration} = useFormat();
  const {data} = useResource<any>('admin/overview');
  const [queueTab, setQueueTab] = useState<'new' | 'pending_agent' | 'overdue'>('new');
  const [period, setPeriod] = useState<Period>('30');
  const from = new Date(Date.now() - (+period - 1) * 86400000).toISOString().slice(0, 10);
  const trend = useResource<any>(can('analytics.view') ? `admin/analytics/tickets?${query({date_from: from})}` : null, [period]);
  const queueParams = queueTab === 'overdue' ? {overdue: true} : {stage: queueTab === 'pending_agent' ? 'pending_agent,reopened,escalated' : 'new'};
  const queue = useResource<any>(can('tickets.view') ? `admin/tickets?${query({...queueParams, size: 6})}` : null, [queueTab]);
  if (!data) return <div className="card">{t('loading')}</div>;
  const open = ['new', 'open', 'pending_agent', 'pending_user', 'escalated', 'reopened'].reduce((sum, s) => sum + (data.tickets[s] ?? 0), 0);

  return <div className="grid">
    <div className="kpis">
      <Card className="kpi"><small>{t('openTickets')}</small><b>{open}</b><span>{t('stage_new')}: {data.tickets.new} · {t('stage_pending_agent')}: {data.tickets.pending_agent}</span></Card>
      <Card className="kpi"><small>{t('overdueTickets')}</small><b className={data.overdue ? 'bad' : ''}>{data.overdue}</b><span>{t('problems24')}: {data.delivery_failures_24h} / {data.errors_24h}</span></Card>
      <Card className="kpi"><small>{t('firstResponseAvg')}</small><b>{duration(data.first_response_7d.average)}</b><span>{t('median')}: {duration(data.first_response_7d.median)}</span></Card>
      <Card className="kpi"><small>{t('slaCompliance')}</small><b>{data.sla_7d === null ? '—' : data.sla_7d + '%'}</b><span>{t('resolutionAvg')}: {duration(data.resolution_7d.average)}</span></Card>
      <Card className="kpi"><small>{t('csat')}</small><b>{data.csat_7d ?? '—'}</b><span>{t('usersTotal')}: {data.users.total} · {data.users.active_7d} {t('activeWeek')}</span></Card>
    </div>

    <div className="grid three">
      <Card title={t('queueTitle')} actions={<Pills value={queueTab} onChange={setQueueTab}
        options={[['new', t('tabNew')], ['pending_agent', t('tabWaiting')], ['overdue', t('tabOverdue')]]}/>}>
        {queue.data?.items?.length ? <div className="list">{queue.data.items.map((ticket: any) =>
          <button key={ticket.id} className="list-row" onClick={() => openTicket(ticket.id)}>
            <span><b>{ticket.number}</b> {ticket.subject || '—'}<small>{ticket.user?.name}</small></span>
            <span className="row-end"><PriorityBadge priority={ticket.priority}/><StatusBadge status={ticket.status}/></span>
          </button>)}</div> : <Empty/>}
      </Card>

      <Card title={t('workload')}>
        {data.agents.length ? <div className="agent-grid">{data.agents.map((agent: any) =>
          <div key={agent.id ?? 'none'} className="agent-tile">
            <b>{agent.id ? agent.name : t('unassigned')}</b>
            <span className="big">{agent.open_now}</span><small>{t('openNow')}</small>
            <small>{agent.replies} {t('repliesWeek')} · SLA {agent.sla_compliance === null ? '—' : agent.sla_compliance + '%'}</small>
          </div>)}</div> : <Empty/>}
      </Card>

      <Card className="tip" title={t('faqTip')} hint={t('faqTipText')}>
        {data.faq?.length ? <ol>{data.faq.map((f: any) => <li key={f.id}>{f.title} <small>· {f.count}</small></li>)}</ol> : <Empty/>}
        {can('analytics.view') && <button className="btn-primary" onClick={() => go('analytics')}>{t('toReports')}</button>}
        <div className="stats-line">{t('messages24')}: {data.messages_24h.in} {t('incoming')} · {data.messages_24h.out} {t('outgoing')}</div>
      </Card>
    </div>

    {can('analytics.view') && <Card title={t('trend')} actions={<Pills value={period} onChange={setPeriod}
      options={[['7', '1W'], ['30', '1M'], ['182', '6M'], ['365', '1Y']]}/>}>
      <AreaChart points={trend.data?.trend ?? data.trend} series={[{key: 'created', label: t('createdSeries'), color: '#c7a2c4'},
        {key: 'resolved', label: t('resolvedSeries'), color: '#5ad19a'}]}/>
    </Card>}
  </div>;
}
