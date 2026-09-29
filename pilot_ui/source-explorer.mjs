// A view over one owned frozen family. Browsing never submits a question.
import {tierBlocks,blockTextPresentation,hasSupportedAnswer} from './core.mjs';

export function citedEvidenceIds(tier) {
  if(tier?.state!=='complete'||!hasSupportedAnswer(tier))return new Set();
  return new Set(tierBlocks(tier).flatMap(block=>{
    const view=blockTextPresentation(tier,block);
    return [...view.parts,view.separateCitation,...(view.separateCitations||[])].filter(part=>part?.evidence)
      .map(part=>part.evidence.evidence_id||part.evidence.citation_id);
  }));
}
export function recordUsed(record, family, pending) {
  if (pending) return null;
  const used=citedEvidenceIds(family?.short);
  return (record.evidence_ids || [record.evidence_id]).some(id=>used.has(id));
}

export function createSourceExplorer({api, family, pending, t, label, icon, onChange, openOriginal, element}) {
  const pageSize=10;
  let epoch=0, current=null, sources=[], selected=null, pages=new Map(), opened=false, summaryLoading=false, summaryError=false;
  const panel=element('beta-explorer'), badges=element('explorer-badges'), list=element('explorer-list');
  const node=(tag,text,attrs={})=>{const value=document.createElement(tag);if(text!=null)value.textContent=text;
    for(const [key,item] of Object.entries(attrs))value.setAttribute(key,String(item));return value;};
  const fid=()=>family()?.answer_family_id;
  function reset(){epoch++;current=null;sources=[];selected=null;pages=new Map();opened=false;summaryLoading=false;summaryError=false;
    panel.hidden=true;badges.replaceChildren();list.replaceChildren();}
  function render(){
    const focusedSource=document.activeElement?.getAttribute('data-source');
    panel.hidden=!opened || current!==fid();
    element('expand-button').setAttribute('aria-expanded',String(opened));
    element('expand-button').setAttribute('aria-controls','beta-explorer');
    element('expand-button').querySelector('span').textContent=t(opened?'beta_less':'beta_more');
    const hasAnswer=hasSupportedAnswer(family()?.short);
    element('explorer-heading').textContent=t(!pending()&&!hasAnswer?'beta_other_matches':'beta_sources_heading');
    element('explorer-note').textContent=t(pending()?'beta_sources_pending':'beta_sources_scope');
    element('explorer-status').textContent=summaryLoading?t('loading'):summaryError?t('matches_unavailable'):sources.length?'':t('no_returned_matches');
    element('explorer-retry').hidden=!summaryError;
    badges.replaceChildren();
    for(const source of sources){const name=label(source), count=Number.isInteger(source.returned_count)?' · '+source.returned_count:'';
      const button=node('button',name+count,{type:'button',class:'explorer-badge','data-source':source.source_slug,
        'aria-pressed':source.source_slug===selected,'aria-controls':'explorer-records'});
      if(icon){button.textContent='';button.append(node('span',icon(source),{class:'source-symbol','aria-hidden':'true'}),node('span',name+count));}
      button.addEventListener('click',()=>select(source.source_slug));badges.append(button);if(focusedSource===source.source_slug)button.focus?.({preventScroll:true});}
    element('explorer-records').hidden=!selected;
    if(!selected)return;
    const source=sources.find(row=>row.source_slug===selected), cache=pages.get(selected), page=cache?.pages.get(cache.offset);
    element('explorer-source-heading').textContent=source?label(source):'';
    list.replaceChildren();
    for(const record of page?.items || []){
      const used=recordUsed(record,family(),pending()), item=node('li',null,{class:'explorer-record','data-record-id':record.record_id,'data-used':used===null?'pending':used});
      const button=node('button',null,{type:'button',class:'explorer-entry','data-evidence-id':record.evidence_id});
      button.append(node('span','›',{class:'entry-arrow','aria-hidden':'true'}),node('span',record.title||t('source_detail'),{class:'entry-title'}));
      button.addEventListener('click',()=>openOriginal(record));item.append(button);
      if(used===true)item.append(node('span',t('match_used'),{class:'entry-used'}));
      if(record.edition && /^\d{4}$/.test(String(record.edition)))item.append(node('span',t('edition')+' '+record.edition,{class:'entry-edition'}));
      // Source prose is opened in the existing original drawer, never flattened
      // into every entry of a long result list.
      list.append(item);
    }
    element('explorer-page-status').textContent=cache?.loading?t('loading'):cache?.error?t('matches_unavailable'):
      page?t('beta_records_page',{start:page.items.length?cache.offset+1:0,end:cache.offset+page.items.length,total:page.returned_count??source?.returned_count??'—'}):'';
    const previous=element('explorer-previous'), next=element('explorer-next');
    previous.hidden=!cache || cache.offset===0;previous.disabled=!!cache?.loading;
    next.hidden=!cache?.error&&!Number.isInteger(page?.next_offset);next.disabled=!!cache?.loading;
    next.textContent=t(cache?.error?'retry':'beta_more_records');
  }
  async function summaries(){
    if(summaryLoading || !current)return;
    const requestEpoch=epoch,id=current;summaryLoading=true;summaryError=false;render();
    try{const value=await api('/families/'+encodeURIComponent(id)+'/sources');if(requestEpoch!==epoch || id!==fid())return;
      sources=Array.isArray(value.sources)?value.sources.filter(row=>typeof row.source_slug==='string'&&row.source_slug):[];onChange?.();
    }catch{if(requestEpoch===epoch)summaryError=true;}
    finally{if(requestEpoch===epoch){summaryLoading=false;render();}}
  }
  async function load(source,offset){
    let cache=pages.get(source);if(!cache){cache={offset:0,pages:new Map(),loading:false,error:false};pages.set(source,cache);}
    if(cache.loading)return;cache.offset=offset;
    if(cache.pages.has(offset)){render();return;}
    const requestEpoch=epoch,id=current;cache.loading=true;cache.error=false;render();
    try{const data=await api('/families/'+encodeURIComponent(id)+'/matches?source='+encodeURIComponent(source)+'&offset='+offset+'&limit='+pageSize);
      if(requestEpoch!==epoch || id!==fid())return;
      cache.pages.set(offset,{items:Array.isArray(data.items)?data.items:[],returned_count:data.returned_count,
        next_offset:Number.isInteger(data.next_offset)&&data.next_offset>offset?data.next_offset:null});
    }catch{if(requestEpoch===epoch)cache.error=true;}
    finally{if(requestEpoch===epoch){cache.loading=false;render();}}
  }
  function select(source){selected=source;const cache=pages.get(source);load(source,cache?.offset||0);}
  element('explorer-previous').addEventListener('click',()=>{const cache=pages.get(selected);if(cache)load(selected,Math.max(0,cache.offset-pageSize));});
  element('explorer-next').addEventListener('click',()=>{const cache=pages.get(selected),page=cache?.pages.get(cache.offset);
    if(cache)load(selected,cache.error?cache.offset:page?.next_offset);});
  element('explorer-retry').addEventListener('click',summaries);
  return {reset,render,toggle(){opened=!opened;render();if(opened&&!sources.length&&!summaryLoading)summaries();},
    sync(value){if(!value.matches_available){if(current!==value.answer_family_id)reset();return;}
      if(current!==value.answer_family_id){reset();current=value.answer_family_id;summaries();}else render();},
    get hasResults(){return current===fid()&&sources.some(source=>source.returned_count>0);},get open(){return opened;}};
}
