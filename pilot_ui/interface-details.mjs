import {sourceKey,tierBlocks,blockTextPresentation,answerNotes,pendingStates,jobState} from './core.mjs';

export function sourcePresentation(registry,item,locale='sl') {
  const key=sourceKey(item), alias=key==='epravopis-slovar'?key:key.startsWith('epravopis')?'epravopis':key;
  const known=registry.sources?.find(source=>source.suggested_key===alias);
  const label=known?.labels?.[locale]||item?.source_label||item?.label||item?.display_name||key;
  return {key,canonicalKey:alias,label,short:known?.badge_default||label,initials:known?.initials_fallback||label.slice(0,2),
    ...(typeof known?.coverage_note?.[locale]==='string'?{coverageNote:known.coverage_note[locale]}:{})};
}

// The server supplies a closed text/cell projection, never trusted markup.
// Even a malformed response can only create known tags and numeric spans.
export function appendSourceReaderBlocks(container,blocks,element) {
  for(const block of blocks||[]) {
    if(block?.kind==='table') {
      if(!Array.isArray(block.rows)||block.rows.length>10000)throw Error('Invalid source table');
      const frame=element('div',null,{class:'source-table-scroll',tabindex:'0'}),table=element('table',null,{class:'source-table'});
      if(block.heading)table.append(element('caption',block.heading));
      const body=element('tbody');let count=0;
      for(const cells of block.rows){if(!Array.isArray(cells)||(count+=cells.length)>10000)throw Error('Invalid source cells');
        const row=element('tr');for(const cell of cells){
          if(!['th','td'].includes(cell?.kind)||typeof cell.text!=='string'||!Number.isInteger(cell.rowspan)||cell.rowspan<0||cell.rowspan>1000||!Number.isInteger(cell.colspan)||cell.colspan<1||cell.colspan>1000)throw Error('Invalid source cell');
          const attrs={rowspan:cell.rowspan,colspan:cell.colspan};
          if(['row','col','rowgroup','colgroup'].includes(cell.scope))attrs.scope=cell.scope;
          row.append(element(cell.kind,cell.text,attrs));
        }body.append(row);
      }table.append(body);frame.append(table);container.append(frame);
    }else{if(block.heading)container.append(element('h4',block.heading));container.append(element('blockquote',typeof block.text==='string'?block.text:''));}
  }
}

export function legendSources(registry,configured,locale='sl') {
  const enabled=new Set((Array.isArray(configured)?configured:[]).map(item=>sourcePresentation(registry,item,locale).canonicalKey));
  return (registry.sources||[]).filter(source=>enabled.has(source.suggested_key))
    .map(source=>sourcePresentation(registry,source.suggested_key,locale));
}

// Use the same rendered citation mapping as the answer, including visible
// qualification citations. Discovery matches are deliberately not an input.
export function answerCopyText(visibleText,tier,label,sourcesHeading) {
  const references=tierBlocks(tier).flatMap(block=>{
    const view=blockTextPresentation(tier,block);
    return [...view.parts,view.separateCitation,...(view.separateCitations||[])].filter(part=>part?.evidence);
  });
  const noteRecords=new Set();
  for(const note of answerNotes(tier))for(const evidence of note.evidence){
    const number=evidence.local_number||evidence.number||evidence.note_number;
    const identity=evidence.evidence_id||evidence.citation_id||evidence.passage_id;
    const record=JSON.stringify([evidence.source_slug,evidence.external_id||identity,evidence.source_revision]);
    if(Number.isInteger(number)&&number>0&&!noteRecords.has(record)){noteRecords.add(record);references.push({number,evidence});}
  }
  const credits=new Map();
  for(const {number,evidence} of references){
    if(!credits.has(String(number))){
      const name=label(evidence),title=evidence.title||evidence.entry_title||evidence.headword;
      credits.set(String(number),'['+number+'] '+name+(title?' — '+title:''));
    }
  }
  const body=String(visibleText||'').trim();
  return credits.size?body+'\n\n'+sourcesHeading+'\n'+[...credits.values()].join('\n'):body;
}

export function longerWaitMessage(job,startedAt,now,hasMatches,t) {
  if(job?.cancel_requested===true||!pendingStates.has(jobState(job))||!Number.isFinite(startedAt)||now-startedAt<20000)return '';
  return t('answer_wait_long')+(hasMatches?' '+t('answer_wait_sources'):'');
}

export function releaseLabel(release,locale,t) {
  const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(release.date||'');
  if(!match||!release.version)return '';
  const [,year,month,day]=match;
  const date=locale==='it'?Number(day)+'.'+Number(month)+'.'+year:Number(day)+'. '+Number(month)+'. '+year;
  return t('public_release',{version:release.version,date});
}

export function historyCreatedDate(family) {
  const raw=family?.created_at??family?.created;
  if(raw===null||raw===undefined||raw==='')return null;
  const value=typeof raw==='number'&&raw<1e12?raw*1000:raw;
  const date=new Date(value);
  return Number.isFinite(date.getTime())?date:null;
}
