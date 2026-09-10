// Source and model output is always rendered as text, never executable markup.
const $=id=>document.getElementById(id);
const text=(tag,value,cls)=>{const el=document.createElement(tag);el.textContent=value??'';if(cls)el.className=cls;return el;};
let health=null;
async function request(path,payload){
  const r=await fetch(path,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(123000)}:{signal:AbortSignal.timeout(10000)});
  if(!r.ok)throw new Error(`Storitev trenutno ni na voljo (${r.status}).`);
  return r.json();
}
function safeLink(url,label){
  const a=text('a',label);try{const u=new URL(url);if(u.protocol==='https:'&&['svetovalnica.zrc-sazu.si','omp.zrc-sazu.si','doi.org'].includes(u.hostname)){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}}catch{}
  return a;
}
function sourceInfo(p){
  const page=p.span?.page_index??p.span?.anchors?.[0]?.page_index;
  const signed=Array.isArray(p.signed_date_text)?p.signed_date_text.join('; '):p.signed_date_text;
  const date=signed?`Podpis: ${signed}`:p.date?`${p.source_slug==='epravopis'?'Objava':'Objava na forumu'}: ${p.date.slice(0,10)}`:'Datum ni naveden';
  return [p.source_slug==='epravopis'?'ePravopis 2025 · letni prirastek':'Jezikovna svetovalnica',p.authors?.join(', ')||'Avtor ni naveden',date,p.source_status,Number.isInteger(page)?`PDF stran ${page+1}`:null].filter(Boolean).join(' · ');
}
async function expand(p,container,button){
  button.disabled=true;
  try{const result=await request('/v1/references/passage?id='+encodeURIComponent(p.passage_id));
    const box=document.createElement('div');box.className='reference-original';
    const seen=new Set();for(const parent of [...result.parent_passages,...(result.page_passages??[])]){if(seen.has(parent.passage_id))continue;seen.add(parent.passage_id);box.append(text('h4',parent.title),text('p',sourceInfo(parent)),text('pre',parent.text));}
    container.append(box);button.remove();
  }catch(e){container.append(text('p',e.message));button.disabled=false;}
}
function render(result){
  const answer=$('reference-answer');answer.replaceChildren();
  const a=result.answer;
  if(a&&!a.no_sufficient_evidence){answer.append(text('h3','Poskusni odgovor'));
    for(const claim of a.claims??[]){const block=document.createElement('section');block.append(text('p',claim.text,'reference-short'));for(const id of claim.evidence_ids){const i=result.passages.findIndex(p=>p.passage_id===id);if(i>=0){const link=text('a',`Odlomek ${i+1}`);link.href=`#reference-passage-${i}`;block.append(link,document.createTextNode(' '));}}
      if(claim.quote){const quote=document.createElement('details');quote.append(text('summary','Preverjeni navedek'),text('blockquote',claim.quote));block.append(quote);}answer.append(block);}
  }else if(result.status==='no_hit'){answer.append(text('p','Pridobljeni odlomki ne vsebujejo dovolj dokazov za odgovor. Poskusite natančnejše vprašanje.'));}
  else if(result.status!=='ok'){answer.append(text('p','Odgovor ni na voljo ali ni prestal preverjanja citatov. Spodaj lahko preverite izvirne odlomke.'));}
  for(const limitation of a?.limitations??[])answer.append(text('p',limitation,'hint'));
  const passages=$('reference-passages');passages.replaceChildren();
  (result.passages??[]).forEach((p,i)=>{const section=document.createElement('article');section.className='reference-passage';section.id=`reference-passage-${i}`;
    section.append(text('h3',`${i+1}. ${p.title}`),text('p',sourceInfo(p),'hint'),text('blockquote',p.text));
    let url=p.url;const page=p.span?.page_index??p.span?.anchors?.[0]?.page_index;
    if(p.source_slug==='epravopis'&&Number.isInteger(page))url='https://omp.zrc-sazu.si/zalozba/catalog/download/2271/9156/3323#page='+(page+1);
    section.append(safeLink(url,'Odpri izvirni vir'),document.createTextNode(' '));
    const button=text('button',p.source_slug==='epravopis'?'Prikaži vse odlomke s te strani':'Prikaži celoten odgovor','quiet');button.addEventListener('click',()=>expand(p,section,button));section.append(button);
    for(const warning of p.warnings??[])section.append(text('p',warning,'hint'));passages.append(section);
  });
  $('reference-diagnostics').textContent=JSON.stringify({status:result.status,snapshot:result.snapshot_id,model:result.model,via:result.via,degraded:result.degraded,diagnostics:result.diagnostics,coverage:health?.coverage,sources:health?.sources},null,2);
}
async function run(answer){
  const question=$('reference-question').value.trim();if(!question)return;
  const source=$('reference-source').value;const selected=$('reference-retrieval').value;const retrieval=selected==='auto'?(answer?'hybrid_rerank':'lexical'):selected;
  const payload={question,sources:source==='all'?['jezikovna_svetovalnica','epravopis']:[source],retrieval,answer_language:$('reference-language').value,model:$('reference-model').value,limit:8};
  $('reference-ask').disabled=$('reference-search').disabled=true;$('reference-status').textContent=answer?'Iskanje dokazov in priprava odgovora …':'Iskanje izvirnih odlomkov …';
  try{const result=await request('/v1/references/'+(answer?'answer':'search'),payload);render(result);$('reference-status').textContent=(result.degraded?.length?'Delno delovanje · ':'')+`${result.passages?.length??0} odlomkov`;}
  catch(e){$('reference-status').textContent=e.message;}
  finally{$('reference-ask').disabled=$('reference-search').disabled=false;}
}
$('reference-ask').addEventListener('click',()=>run(true));$('reference-search').addEventListener('click',()=>run(false));
async function refresh(){
  try{health=await request('/v1/references/health');const n=Object.values(health.sources??{}).reduce((s,v)=>s+(v.passages??0),0);$('reference-ready').textContent=health.fulltext_ready?`${n} indeksiranih odlomkov`:'Indeks virov trenutno ni dosegljiv';
    const models=health.models??{};for(const option of $('reference-model').options){const ready=models[option.value]?.data?.some(m=>m.id===option.value);option.disabled=!ready;if(option.value==='challenger')option.textContent=ready?'Gemma · primerjava':'Gemma · trenutno ni naložen';}
    $('reference-diagnostics').textContent=JSON.stringify({sources:health.sources,coverage:health.coverage,snapshot:health.snapshot_id},null,2);
  }catch{$('reference-ready').textContent='Referenčna storitev ni dosegljiva';}
}
refresh();
