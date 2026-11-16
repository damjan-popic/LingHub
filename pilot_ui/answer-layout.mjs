// Layout over exact saved text. Literal markers and line endings remain in the
// block so source links, copying and codepoint feedback anchors keep their text.
export function answerTextLines(parts) {
  const lines=[{parts:[],ending:''}];
  for(const part of parts){
    if(part.evidence){lines.at(-1).parts.push(part);continue;}
    const pieces=part.text.split('\n');
    for(const [index,text]of pieces.entries()){
      if(index){lines.at(-1).ending='\n';lines.push({parts:[],ending:''});}
      if(text)lines.at(-1).parts.push({...part,text});
    }
  }
  return lines.map(line=>{
    const text=line.parts.map(part=>part.text).join('');
    return {...line,kind:/^[\t ]*[-*•][\t ]+\S/u.test(text)?'list':text.trim()?'paragraph':'separator'};
  });
}

export function appendAnswerParts(parent,parts,element,citation) {
  const append=(node,items)=>{for(const part of items)node.append(part.evidence?citation(part):document.createTextNode(part.text));};
  const lines=answerTextLines(parts);
  if(!lines.some(line=>line.kind==='list')&&!parts.some(part=>!part.evidence&&part.text.includes('\n\n'))){append(parent,parts);return;}
  parent.classList.add('answer-layout');
  for(let index=0;index<lines.length;){
    const first=lines[index];
    if(first.kind==='separator'){append(parent,first.parts);parent.append(document.createTextNode(first.ending));index++;continue;}
    const kind=first.kind,section=element('span',null,{class:kind==='list'?'answer-list':'answer-paragraph',...(kind==='list'?{role:'list'}:{})});
    while(index<lines.length){
      const line=lines[index];
      if(kind==='list'&&line.kind==='separator'){
        let next=index;while(lines[next]?.kind==='separator')next++;
        if(lines[next]?.kind==='list'){append(section,line.parts);section.append(document.createTextNode(line.ending));index++;continue;}
      }
      if(line.kind!==kind)break;
      index++;
      if(kind==='list'){
        const item=element('span',null,{class:'answer-list-item',role:'listitem'});
        append(item,line.parts);section.append(item,document.createTextNode(line.ending));
      }else{
        append(section,line.parts);
        if(index<lines.length&&lines[index].kind===kind)section.append(document.createTextNode(line.ending));
        else{parent.append(section,document.createTextNode(line.ending));break;}
      }
    }
    if(kind==='list')parent.append(section);
  }
}

// Optional server-bound projection. A missing/stale view preserves the full
// saved answer; no role is inferred from words, sources or block order.
export function layeredAnswerSections(tier) {
  const view=tier?.answer_presentation,blocks=tier?.blocks;
  if(view?.version!=='layered-consultation-presentation-v1'||!Array.isArray(view.sections)||!Array.isArray(blocks)||!/^[0-9a-f]{64}$/.test(view.presentation_sha256))return null;
  const byId=new Map(blocks.map(block=>[block.block_id,block])),seen=new Set(),ranges=new Map();
  try{
    const part=(item,roles)=>{
      const block=byId.get(item.block_id),points=Array.from(block.text);
      if(!roles.includes(item.role)||!Number.isInteger(item.start)||!Number.isInteger(item.end)||item.start<0||item.end<=item.start||points.slice(item.start,item.end).join('')!==item.text)throw Error('Stale presentation');
      const spans=ranges.get(item.block_id)||[];spans.push([item.start,item.end]);ranges.set(item.block_id,spans);
      if(['relation','head_context'].includes(item.role)){if(seen.has(item.block_id))throw Error('Repeated body');seen.add(item.block_id);}
    };
    for(const section of view.sections){
      if(section.block_id){if(!byId.has(section.block_id)||seen.has(section.block_id))return null;seen.add(section.block_id);continue;}
      if(!section.primary?.length||!Array.isArray(section.additional))return null;
      const relation=section.primary.some(item=>item.role==='relation');
      if((relation&&section.primary.some(item=>item.role!=='relation'))||(section.additional.length&&!relation))return null;
      part(section.intro,['intro']);part(section.footer,['footer']);
      section.primary.forEach(item=>part(item,['relation','head_context']));section.additional.forEach(item=>part(item,['head_context']));
    }
    if(seen.size!==byId.size)return null;
    for(const [id,spans]of ranges){
      const text=Array.from(byId.get(id).text);let cursor=0;
      for(const [start,end]of spans.sort((a,b)=>a[0]-b[0])){if(start<cursor||!['','\n\n'].includes(text.slice(cursor,start).join('')))return null;cursor=end;}
      if(cursor!==text.length)return null;
    }
    return view.sections;
  }catch{return null;}
}

export function appendLayeredAnswer(parent,tier,element,renderBlock,translate) {
  const sections=layeredAnswerSections(tier);if(!sections){delete parent.dataset.presentationId;return false;}
  const blocks=new Map(tier.blocks.map(block=>[block.block_id,block]));
  const append=(node,part,cited=true)=>renderBlock(node,{...blocks.get(part.block_id),text:part.text},part.start,cited);
  parent.dataset.presentationId=tier.answer_presentation.presentation_sha256;
  for(const section of sections){
    if(section.block_id){renderBlock(parent,blocks.get(section.block_id),0,true);continue;}
    const group=element('div',null,{class:'consultation-scope','data-context-key':section.context_key});
    append(group,section.intro,false);
    for(const part of section.primary)append(group,part);
    if(section.additional.length){
      const details=element('details',null,{class:'additional-lexical-context'});
      details.append(element('summary',tier.answer_presentation.additional_label));
      for(const part of section.additional)append(details,part);
      group.append(details);
    }
    append(group,section.footer,false);parent.append(group);
  }
  const full=element('details',null,{class:'complete-answer'});
  full.append(element('summary',translate('complete_answer')),element('pre',tier.answer_md||tier.short_answer||''));
  parent.append(full);return true;
}
