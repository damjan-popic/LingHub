import {sourceKey,tierBlocks,blockTextPresentation,answerNotes,pendingStates,jobState} from './core.mjs';

export function sourcePresentation(registry,item,locale='sl') {
  const key=sourceKey(item), alias=key.startsWith('epravopis')?'epravopis':key;
  const known=registry.sources?.find(source=>source.suggested_key===alias);
  const label=known?.labels?.[locale]||item?.source_label||item?.label||item?.display_name||key;
  return {key,canonicalKey:alias,label,short:known?.badge_default||label,initials:known?.initials_fallback||label.slice(0,2)};
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
  for(const note of answerNotes(tier))for(const evidence of note.evidence){
    const number=evidence.local_number||evidence.number;
    if(Number.isInteger(number)&&number>0)references.push({number,evidence});
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
