import {useState} from 'react';
import {query} from '../services/api';
import {useT} from '../services/i18n';
import {AreaChart, Card, Empty, ExportButtons, Pills, useAccess, useFormat, useLookups, useName, useResource} from '../components/ui';

type Report = 'tickets' | 'users' | 'faq' | 'agents' | 'campaigns';
// A field only that report has: after switching tabs the previous report stays in memory until the new one arrives,
// and must not be drawn with the new tab's layout.
const SHAPE: Record<Report, string> = {tickets: 'trend', users: 'returning', faq: 'top_questions', agents: 'agents', campaigns: 'campaigns'};

function Bars({data, label}: {data: Record<string, number>; label: (key: string) => string}) {
  const entries = Object.entries(data ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(e => e[1]));
  if (!entries.length) return <Empty/>;
  return <div className="bars">{entries.map(([key, value]) => <div key={key} className="bar-row">
    <span>{label(key)}</span><i style={{width: `${100 * value / max}%`}}/><b>{value}</b></div>)}</div>;
}

function Table({rows, columns}: {rows: any[]; columns: [string, string, ((row: any) => any)?][]}) {
  if (!rows?.length) return <Empty/>;
  return <div className="table-wrap"><table className="table compact"><thead><tr>{columns.map(([key, title]) => <th key={key}>{title}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{columns.map(([key, , render]) => <td key={key}>{render ? render(row) : row[key] ?? '—'}</td>)}</tr>)}</tbody></table></div>;
}

export function Analytics() {
  const {t, lang} = useT();
  const {can} = useAccess();
  const {lookups} = useLookups();
  const name = useName();
  const {duration} = useFormat();
  const [report, setReport] = useState<Report>('tickets');
  const today = new Date().toISOString().slice(0, 10);
  const [filters, setFilters] = useState({date_from: new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10), date_to: today,
    language: '', department_id: '', assignee_id: '', priority: '', status: ''});
  // department, agent, priority and status describe tickets: they apply to the ticket and agent reports only
  const ticketFilters = report === 'tickets' || report === 'agents';
  const params = query(ticketFilters ? filters : {date_from: filters.date_from, date_to: filters.date_to, language: filters.language});
  const {data} = useResource<any>(`admin/analytics/${report}?${params}&lang=${lang}`, [report, params]);
  const set = (change: Partial<typeof filters>) => setFilters({...filters, ...change});
  const statusName = (key: string) => name(lookups.statuses.find(s => s.key === key)?.name) || key;
  const department = (key: string) => key ? name(lookups.departments.find(d => String(d.id) === key)?.name) || key : t('noDepartment');
  const category = (key: string) => key ? name(lookups.categories.find(c => String(c.id) === key)?.name) || key : '—';
  const stat = (s: any) => <>{duration(s?.average)} <small className="muted">/ {duration(s?.median)}</small></>;

  return <div className="grid">
    <Card>
      <div className="filters">
        <Pills value={report} onChange={setReport} options={(['tickets', 'users', 'faq', 'agents', 'campaigns'] as Report[]).map(r => [r, t('r_' + r)])}/>
        <label className="inline">{t('from')}<input type="date" value={filters.date_from} onChange={e => set({date_from: e.target.value})}/></label>
        <label className="inline">{t('to')}<input type="date" value={filters.date_to} onChange={e => set({date_to: e.target.value})}/></label>
        <select value={filters.language} onChange={e => set({language: e.target.value})} aria-label={t('language')}><option value="">{t('language')}: {t('all')}</option>
          {lookups.languages.map(l => <option key={l.code} value={l.code}>{l.flag} {l.native_name}</option>)}</select>
        {ticketFilters && <>
        <select value={filters.department_id} onChange={e => set({department_id: e.target.value})} aria-label={t('department')}><option value="">{t('department')}: {t('all')}</option>
          {lookups.departments.map(d => <option key={d.id} value={d.id}>{name(d.name)}</option>)}</select>
        <select value={filters.assignee_id} onChange={e => set({assignee_id: e.target.value})} aria-label={t('agent')}><option value="">{t('agent')}: {t('all')}</option>
          {lookups.agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
        <select value={filters.priority} onChange={e => set({priority: e.target.value})} aria-label={t('priority')}><option value="">{t('priority')}: {t('all')}</option>
          {lookups.priorities.map(p => <option key={p} value={p}>{t(p)}</option>)}</select>
        <select value={filters.status} onChange={e => set({status: e.target.value})} aria-label={t('status')}><option value="">{t('status')}: {t('all')}</option>
          {lookups.statuses.map(s => <option key={s.key} value={s.key}>{name(s.name)}</option>)}</select></>}
        <span className="grow"/>
        {can('analytics.export') && <ExportButtons route={`admin/analytics/${report}`} params={`${params}&lang=${lang}`}/>}
      </div>
      <small className="muted">{t('rulesDoc')}</small>
    </Card>

    {!data || !(SHAPE[report] in data) ? <div className="card">{t('loading')}</div> : <>
      {report === 'tickets' && <>
        <div className="kpis">
          <Card className="kpi"><small>{t('total')}</small><b>{data.total}</b><span>{t('resolved')} {data.resolved} · {t('reopenedCount')} {data.reopened}</span></Card>
          <Card className="kpi"><small>{t('openTickets')}</small><b>{data.open}</b><span>{t('overdueTickets')}: {data.overdue}</span></Card>
          <Card className="kpi"><small>{t('firstResponse')} ({t('average')} / {t('median')})</small><b>{stat(data.first_response)}</b></Card>
          <Card className="kpi"><small>{t('resolution')} ({t('average')} / {t('median')})</small><b>{stat(data.resolution)}</b></Card>
          <Card className="kpi"><small>{t('slaCompliance')}</small><b>{data.sla_compliance === null ? '—' : data.sla_compliance + '%'}</b><span>{t('csat')}: {data.csat ?? '—'} ({data.ratings})</span></Card>
        </div>
        <Card title={t('trend')}><AreaChart points={data.trend} series={[{key: 'created', label: t('createdSeries'), color: '#c7a2c4'}, {key: 'resolved', label: t('resolvedSeries'), color: '#5ad19a'}]}/></Card>
        <div className="grid two">
          <Card title={t('byStatus')}><Bars data={data.by_status} label={statusName}/></Card>
          <Card title={t('byPriority')}><Bars data={data.by_priority} label={k => t(k)}/></Card>
          <Card title={t('byDepartment')}><Bars data={data.by_department} label={department}/></Card>
          <Card title={t('byCategory')}><Bars data={data.by_category} label={category}/></Card>
        </div>
      </>}
      {report === 'users' && <>
        <div className="kpis">
          <Card className="kpi"><small>{t('total')}</small><b>{data.total}</b></Card>
          <Card className="kpi"><small>{t('newUsers')}</small><b>{data.new}</b></Card>
          <Card className="kpi"><small>{t('activeUsers')}</small><b>{data.active}</b><span>{t('returning')}: {data.returning}</span></Card>
          <Card className="kpi"><small>{t('ticketsPerUser')}</small><b>{data.tickets_per_user}</b></Card>
        </div>
        <div className="grid two">
          <Card title={t('byLanguage')}><Bars data={data.by_language} label={k => k || '—'}/></Card>
          <Card title={t('topCategories')}><Bars data={Object.fromEntries(data.top_categories ?? [])} label={category}/></Card>
        </div>
      </>}
      {report === 'faq' && <div className="grid two">
        <Card title={t('topQuestions')}><Table rows={data.top_questions} columns={[['title', t('name')], ['count', t('count')]]}/></Card>
        <Card title={t('topCategories')}><Table rows={data.top_categories} columns={[['title', t('name')], ['count', t('count')]]}/></Card>
        <Card title={t('paths')}><Table rows={data.paths} columns={[['path', t('paths')], ['count', t('count')]]}/></Card>
        <Card title={t('leadToTickets')}><Table rows={data.lead_to_tickets} columns={[['title', t('name')], ['count', t('count')]]}/></Card>
        <Card title={t('missing')}><Table rows={data.missing} columns={[['query', t('search')], ['count', t('count')]]}/></Card>
      </div>}
      {report === 'agents' && <div className="grid">
        <Card title={t('workload')}><Table rows={data.agents} columns={[['name', t('agent'), r => r.id ? r.name : t('unassigned')], ['tickets', t('tickets')],
          ['open_now', t('openNow')], ['resolved', t('resolved')], ['replies', t('replies')], ['fr', t('firstResponse'), r => stat(r.first_response)],
          ['res', t('resolution'), r => stat(r.resolution)], ['sla', 'SLA', r => r.sla_compliance === null ? '—' : r.sla_compliance + '%']]}/></Card>
        <Card title={t('departments')}><Table rows={data.departments} columns={[['name', t('department'), r => r.id ? r.name : t('noDepartment')], ['tickets', t('tickets')],
          ['resolved', t('resolved')], ['fr', t('firstResponse'), r => stat(r.first_response)], ['res', t('resolution'), r => stat(r.resolution)],
          ['sla', 'SLA', r => r.sla_compliance === null ? '—' : r.sla_compliance + '%']]}/></Card>
      </div>}
      {report === 'campaigns' && <div className="grid two">
        <Card title={t('campaigns')}><Table rows={data.campaigns} columns={[['name', t('name')], ['status', t('status'), r => t('c_' + r.status)],
          ['recipients', t('recipients')], ['sent', t('d_sent')], ['failed', t('d_failed')], ['blocked', t('d_blocked')]]}/></Card>
        <Card title={t('errorsByType')}><Table rows={data.errors} columns={[['error', t('errors')], ['count', t('count')]]}/></Card>
      </div>}
    </>}
  </div>;
}
