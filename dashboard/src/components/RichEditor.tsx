import {useState} from 'react';
import {api, upload} from '../services/api';
import {useT} from '../services/i18n';
import {Empty, LangText, Modal, useFormat, useName, useNotice, useResource} from './ui';

export type Block = {type: string; text: Record<string, string>; assets: number[]; enabled: boolean};
export type ButtonDef = {label: Record<string, string>; action: string; value: string};
export type Rich = {blocks: Block[]; buttons: ButtonDef[]};
export const emptyRich = (): Rich => ({blocks: [], buttons: []});

const TYPES = ['text', 'photo', 'album', 'video', 'audio', 'voice', 'document', 'animation'];
const ACTIONS = ['url', 'node', 'faq', 'support', 'tickets', 'menu', 'language'];
const KINDS: Record<string, string[]> = {photo: ['photo'], album: ['photo', 'video'], video: ['video'], audio: ['audio'], voice: ['voice', 'audio'],
  document: ['document', 'photo', 'video', 'audio', 'voice', 'animation'], animation: ['animation', 'video'], text: []};

/** Pick files from the media library (or upload a new one right here). */
export function MediaPicker({kinds, multiple, onPick, onClose}: {kinds: string[]; multiple?: boolean; onPick: (ids: number[]) => void; onClose: () => void}) {
  const {t} = useT();
  const {size} = useFormat();
  const {data, reload} = useResource<any[]>('admin/media');
  const [chosen, setChosen] = useState<number[]>([]);
  const {run, view} = useNotice();
  const items = (data ?? []).filter(a => kinds.includes(a.kind));
  const toggle = (id: number) => setChosen(multiple ? (chosen.includes(id) ? chosen.filter(x => x !== id) : [...chosen, id].slice(0, 10)) : [id]);
  return <Modal title={t('chooseMedia')} onClose={onClose} wide footer={<>
    {view}
    <label className="btn-ghost">{t('upload')}<input type="file" hidden onChange={e => {
      const file = e.target.files?.[0];
      if (file) run(() => upload('admin/media', file).then(reload), t('saved'));
    }}/></label>
    <button className="btn-primary" disabled={!chosen.length} onClick={() => { onPick(chosen); onClose(); }}>{t('apply')}</button></>}>
    {items.length ? <div className="media-grid">{items.map(a => <button key={a.id} type="button" className={'media-tile' + (chosen.includes(a.id) ? ' active' : '')} onClick={() => toggle(a.id)}>
      {a.kind === 'photo' ? <img src={`/api/admin/media/${a.id}/file`} alt=""/> : <span className="media-kind">{t('b_' + a.kind)}</span>}
      <small>{a.filename}<br/>{size(a.size)}</small>
      {chosen.includes(a.id) && <b className="media-order">{chosen.indexOf(a.id) + 1}</b>}
    </button>)}</div> : <Empty/>}
  </Modal>;
}

export function AssetChip({id, onRemove}: {id: number; onRemove?: () => void}) {
  const {data} = useResource<any[]>('admin/media');
  const asset = data?.find(a => a.id === id);
  return <span className="chip">{asset?.kind === 'photo' && <img src={`/api/admin/media/${id}/file`} alt=""/>}{asset?.filename ?? `#${id}`}
    {onRemove && <button type="button" onClick={onRemove} aria-label="Remove">✕</button>}</span>;
}

/** FAQ questions as options (for buttons and redirects). */
export function useFaqOptions() {
  const {data} = useResource<any>('admin/faq');
  const name = useName();
  const nodes: any[] = data?.nodes ?? [];
  const label = (n: any): string => {
    const parent = nodes.find(p => p.id === n.parent_id);
    return (parent ? label(parent) + ' › ' : '') + name(n.title);
  };
  return nodes.map(n => ({id: n.id, label: label(n)})).sort((a, b) => a.label.localeCompare(b.label));
}

export function RichEditor({value, onChange, allowButtons = true}: {value: Rich; onChange: (v: Rich) => void; allowButtons?: boolean}) {
  const {t} = useT();
  const [picker, setPicker] = useState<number | null>(null);
  const faqOptions = useFaqOptions();
  const blocks = value.blocks ?? [], buttons = value.buttons ?? [];
  const setBlock = (i: number, change: Partial<Block>) => onChange({...value, blocks: blocks.map((b, j) => j === i ? {...b, ...change} : b)});
  const move = <T,>(list: T[], i: number, d: number) => { const copy = [...list]; const [x] = copy.splice(i, 1); copy.splice(Math.max(0, Math.min(copy.length, i + d)), 0, x); return copy; };
  const setButton = (i: number, change: Partial<ButtonDef>) => onChange({...value, buttons: buttons.map((b, j) => j === i ? {...b, ...change} : b)});

  return <div className="rich">
    <div className="rich-section"><b>{t('blocks')}</b><small>{t('htmlHint')}</small></div>
    {blocks.map((block, i) => <div className="rich-block" key={i}>
      <div className="rich-row">
        <select value={block.type} onChange={e => setBlock(i, {type: e.target.value, assets: e.target.value === 'text' ? [] : block.assets})}>
          {TYPES.map(type => <option key={type} value={type}>{t('b_' + type)}</option>)}
        </select>
        <label className="inline"><input type="checkbox" checked={block.enabled !== false} onChange={e => setBlock(i, {enabled: e.target.checked})}/>{t('enabled')}</label>
        <span className="grow"/>
        <button type="button" className="icon-btn small" onClick={() => onChange({...value, blocks: move(blocks, i, -1)})} aria-label={t('up')}>↑</button>
        <button type="button" className="icon-btn small" onClick={() => onChange({...value, blocks: move(blocks, i, 1)})} aria-label={t('down')}>↓</button>
        <button type="button" className="icon-btn small" onClick={() => onChange({...value, blocks: blocks.filter((_, j) => j !== i)})} aria-label={t('delete')}>✕</button>
      </div>
      {block.type !== 'text' && <div className="rich-row wrap">
        {block.assets.map(id => <AssetChip key={id} id={id} onRemove={() => setBlock(i, {assets: block.assets.filter(a => a !== id)})}/>)}
        <button type="button" className="btn-ghost small" onClick={() => setPicker(i)}>{t('chooseMedia')}</button>
      </div>}
      <LangText value={block.text ?? {}} onChange={text => setBlock(i, {text})} multiline rows={block.type === 'text' ? 5 : 2}
                placeholder={block.type === 'text' ? t('b_text') : t('caption')} maxLength={block.type === 'text' ? 4096 : 1024}/>
    </div>)}
    <button type="button" className="btn-ghost small" onClick={() => onChange({...value, blocks: [...blocks, {type: 'text', text: {}, assets: [], enabled: true}]})}>+ {t('addBlock')}</button>

    {allowButtons && <>
      <div className="rich-section"><b>{t('buttons')}</b></div>
      {buttons.map((button, i) => <div className="rich-block" key={i}>
        <div className="rich-row wrap">
          <select value={button.action} onChange={e => setButton(i, {action: e.target.value, value: ''})}>
            {ACTIONS.map(action => <option key={action} value={action}>{t('a_' + action)}</option>)}
          </select>
          {button.action === 'url' && <input type="text" placeholder="https://" value={button.value} onChange={e => setButton(i, {value: e.target.value})}/>}
          {button.action === 'node' && <select value={button.value} onChange={e => setButton(i, {value: e.target.value})}>
            <option value="">—</option>{faqOptions.map(o => <option key={o.id} value={String(o.id)}>{o.label}</option>)}</select>}
          <span className="grow"/>
          <button type="button" className="icon-btn small" onClick={() => onChange({...value, buttons: move(buttons, i, -1)})} aria-label={t('up')}>↑</button>
          <button type="button" className="icon-btn small" onClick={() => onChange({...value, buttons: buttons.filter((_, j) => j !== i)})} aria-label={t('delete')}>✕</button>
        </div>
        <LangText value={button.label ?? {}} onChange={label => setButton(i, {label})} placeholder={t('buttonLabel')} maxLength={64}/>
      </div>)}
      <button type="button" className="btn-ghost small" onClick={() => onChange({...value, buttons: [...buttons, {label: {}, action: 'url', value: ''}]})}>+ {t('addButton')}</button>
    </>}

    {picker !== null && <MediaPicker kinds={KINDS[blocks[picker]?.type] ?? []} multiple={blocks[picker]?.type === 'album'}
      onPick={ids => setBlock(picker, {assets: blocks[picker].type === 'album' ? [...blocks[picker].assets, ...ids].slice(0, 10) : ids.slice(0, 1)})}
      onClose={() => setPicker(null)}/>}
  </div>;
}

/** Sends content to the editor's own Telegram. */
export function PreviewButton({content, nodeId}: {content?: Rich; nodeId?: number}) {
  const {t, lang} = useT();
  const {run, view} = useNotice();
  return <span className="actions">{view}
    <button type="button" className="btn-ghost small" onClick={() => run(() => api('admin/content/preview', 'POST',
      {content: nodeId === undefined ? content : null, node_id: nodeId ?? null, language: lang}), t('previewSent'))}>{t('previewTelegram')}</button></span>;
}
