import {useState} from 'react';
import {query} from '../services/api';
import {useT} from '../services/i18n';
import {Card, Empty, Pager, useFormat, useResource} from '../components/ui';

export function Audit() {
  const {t} = useT();
  const {date} = useFormat();
  const [filters, setFilters] = useState({action: '', staff_name: '', target: '', date_from: ''}), [page, setPage] = useState(1);
  const {data} = useResource<any>(`admin/audit?${query({...filters, page})}`, [filters, page]);
  const set = (change: Partial<typeof filters>) => { setFilters({...filters, ...change}); setPage(1); };
  return <div className="grid">
    <Card><div className="filters">
      <input type="text" placeholder={t('action') + ' (ticket., user., team., settings., auth.)'} value={filters.action} onChange={e => set({action: e.target.value})}/>
      <input type="text" placeholder={t('staff')} value={filters.staff_name} onChange={e => set({staff_name: e.target.value})}/>
      <input type="text" placeholder={t('target')} value={filters.target} onChange={e => set({target: e.target.value})}/>
      <label className="inline">{t('from')}<input type="date" value={filters.date_from} onChange={e => set({date_from: e.target.value})}/></label>
    </div></Card>
    <Card>
      {data?.items?.length ? <div className="table-wrap"><table className="table compact">
        <thead><tr><th>{t('date')}</th><th>{t('staff')}</th><th>{t('action')}</th><th>{t('target')}</th><th>{t('details')}</th><th>{t('ip')}</th></tr></thead>
        <tbody>{data.items.map((a: any) => <tr key={a.id}><td>{date(a.created_at)}</td><td>{a.staff}</td><td><code>{a.action}</code></td>
          <td>{a.target_type} {a.target_id}</td><td className="pre small">{Object.keys(a.details ?? {}).length ? JSON.stringify(a.details) : ''}</td><td>{a.ip}</td></tr>)}</tbody>
      </table></div> : <Empty/>}
      {data && <Pager page={page} size={100} total={data.total} onPage={setPage}/>}
    </Card>
  </div>;
}
