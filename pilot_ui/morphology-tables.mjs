// A view over captured source slots. No prose parsing, paradigm generation or
// source lookup occurs here; equal spellings never join lexical occurrences.
const axesOrder={case:['nominative','genitive','dative','accusative','locative','instrumental'],
  number:['singular','dual','plural'],person:['first','second','third'],gender:['masculine','feminine','neuter']};
const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const feature=form=>form.normalized_features||{};
const values=(forms,key)=>[...new Set(forms.filter(form=>own(feature(form),key)).map(form=>feature(form)[key]))];
const ordered=(items,key)=>[...(axesOrder[key]||[]).filter(value=>items.includes(value)),...items.filter(value=>!(axesOrder[key]||[]).includes(value))];
const label=(forms,key,value)=>forms.find(form=>same(feature(form)[key],value)&&form.feature_labels?.[key])?.feature_labels[key]||key+'='+JSON.stringify(value);

export function morphologyControls(row){
  if(!row.forms?.every(form=>form.normalized_features&&typeof form.normalized_features==='object'))return [];
  return ['vform','degree','gender'].filter(key=>key!=='gender'||row.grammatical_type!=='verb')
    .map(key=>({key,values:values(row.forms,key)})).filter(control=>control.values.length>1);
}

export function initialMorphologySelection(row){
  return Object.fromEntries(morphologyControls(row).map(control=>[control.key,
    control.values.includes(row.paradigm_focus?.[control.key])?row.paradigm_focus[control.key]:control.values[0]]));
}

export function morphologyTableView(row,selection=initialMorphologySelection(row)){
  const forms=row.forms||[],controls=morphologyControls(row);
  const typed=forms.every(form=>form.normalized_features&&typeof form.normalized_features==='object');
  if(!typed)return {typed:false,controls:[],shown:forms.length,total:forms.length,groups:[{forms,axes:null,captionFeatures:[],unspecified:[]}]};
  const selected=forms.filter(form=>controls.every(({key})=>selection[key]==null||!own(feature(form),key)||same(feature(form)[key],selection[key])));
  const grouping=['vform','degree','gender'].filter(key=>key!=='gender'||row.grammatical_type!=='verb')
    .filter(key=>values(forms,key).length>1||key==='vform'&&values(forms,key).length);
  const groups=new Map();
  for(const form of selected){
    const key=JSON.stringify(grouping.map(name=>[own(feature(form),name),feature(form)[name]]));
    if(!groups.has(key))groups.set(key,[]);groups.get(key).push(form);
  }
  const tables=[];
  for(const group of groups.values()){
    const candidates=[['case','number'],['person','number'],['gender','number']];
    const axes=candidates.find(pair=>group.some(form=>pair.every(key=>own(feature(form),key))));
    const matrix=axes?group.filter(form=>axes.every(key=>own(feature(form),key))):[];
    const remainder=axes?group.filter(form=>!axes.every(key=>own(feature(form),key))):group;
    for(const [members,pair]of [[matrix,axes],[remainder,null]]){
      if(!members.length)continue;
      const keys=[...new Set(members.flatMap(form=>Object.keys(feature(form))))];
      const constant=keys.filter(key=>!(pair||[]).includes(key)&&members.every(form=>own(feature(form),key)&&same(feature(form)[key],feature(members[0])[key]))
        &&!same(row.grammatical_features?.[key],feature(members[0])[key]));
      const unspecified=grouping.filter(key=>!own(feature(members[0]),key));
      tables.push({forms:members,axes:pair,captionFeatures:constant,unspecified,
        ...(pair?{rows:ordered(values(members,pair[0]),pair[0]),columns:ordered(values(members,pair[1]),pair[1])}:{})});
    }
  }
  return {typed:true,controls,shown:selected.length,total:forms.length,groups:tables};
}

function sourceLabel(node){
  if(!node||typeof node!=='object')return '';
  return [node.tag,...Object.entries(node.attributes||{}).map(([key,value])=>key+'='+value),node.text,
    ...(node.children||[]).map(sourceLabel)].filter(value=>value!=null&&String(value).trim()).join(' · ');
}

function appendVariants(parent,form,e,t,language){
  if(!Array.isArray(form.variants)){
    parent.append(e('span',form.text,language?{lang:language}:{}));
    if(form.pattern_memberships?.length>1)for(const membership of form.pattern_memberships)
      parent.append(e('small',membership.title+': '+membership.forms.join(' / '),{class:'morphology-condition'}));
    return;
  }
  const patterns=[...new Set(form.variants.flatMap(variant=>variant.source_pattern_ids||[]))];
  for(const [index,variant]of form.variants.entries()){
    if(index)parent.append(' / ');
    const node=e('span',null,{class:'morphology-variant'});
    if(variant.source_locator)node.dataset.sourceLocator=variant.source_locator;
    node.append(e('span',variant.form,language?{lang:language}:{}));
    const notes=(variant.labels||[]).map(sourceLabel).filter(Boolean);
    notes.push(...Object.entries(variant.source_attributes||{}).filter(([key])=>!['id','morphologyPatterns'].includes(key)).map(([key,value])=>key+'='+value));
    if(patterns.length>1)notes.push(...(variant.source_pattern_ids||[]).map(id=>form.pattern_memberships?.find(member=>member.source_pattern_id===id)?.title||t('morphology_source_pattern')+' '+id));
    if(notes.length)node.append(e('small',' ('+notes.join('; ')+')',{class:'morphology-variant-note'}));
    parent.append(node);
  }
}

export function appendMorphologyTables(parent,row,{element:e,translate:t}){
  const disclosure=e('details',null,{class:'morphology-tables'}),summary=e('summary',t('morphology_inflection'));
  disclosure.append(summary);parent.append(disclosure);
  const selection=initialMorphologySelection(row),controls=morphologyControls(row);
  const toolbar=e('div',null,{class:'morphology-controls'}),content=e('div'),status=e('p','',{class:'small muted',role:'status'});
  for(const control of controls){
    const wrap=e('label',t('morphology_'+control.key)),select=e('select',null,{'data-morphology-control':control.key});
    select.append(e('option',t('morphology_all'),{value:''}));
    for(const value of control.values)select.append(e('option',label(row.forms,control.key,value),{value:JSON.stringify(value)}));
    select.value=JSON.stringify(selection[control.key]);
    select.addEventListener('change',()=>{selection[control.key]=select.value===''?null:JSON.parse(select.value);render();});
    wrap.append(select);toolbar.append(wrap);
  }
  if(controls.length)disclosure.append(toolbar);
  disclosure.append(content,status);
  function render(){
    content.replaceChildren();const view=morphologyTableView(row,selection);
    status.textContent=t('morphology_view_count',{shown:view.shown,total:view.total});
    for(const group of view.groups){
      const frame=e('div',null,{class:'source-table-scroll morphology-table-scroll',tabindex:'0'}),table=e('table',null,{class:'source-table morphology-table'});
      const names=group.captionFeatures.map(key=>label(group.forms,key,feature(group.forms[0])[key]));
      names.push(...group.unspecified.map(key=>t('morphology_unspecified',{feature:t('morphology_'+key)})));
      const caption=e('caption',[row.title,...names].filter(Boolean).join(' · '));table.append(caption);
      const head=e('thead'),header=e('tr'),body=e('tbody');
      const firstTitle=group.axes?t('morphology_'+group.axes[0]):t('morphology_description');
      header.append(e('th',firstTitle,{scope:'col'}));
      for(const value of group.axes?group.columns:[null])header.append(e('th',group.axes?label(group.forms,group.axes[1],value):t('morphology_forms'),{scope:'col'}));
      head.append(header);table.append(head,body);
      function observation(cell,form){
        const slot=e('div',null,{class:'morphology-slot'});
        if(form.slot_locator!=null)slot.dataset.slotLocator=form.slot_locator;
        if(row.source_occurrence_key)slot.dataset.sourceOccurrence=row.source_occurrence_key;
        if(form.original_msd!=null)slot.dataset.originalMsd=form.original_msd;
        const conditions=view.typed&&group.axes?Object.keys(feature(form)).filter(key=>!group.axes.includes(key)&&!group.captionFeatures.includes(key)
          &&!same(row.grammatical_features?.[key],feature(form)[key])).map(key=>label([form],key,feature(form)[key])):[];
        if(conditions.length)slot.append(e('small',conditions.join(' · '),{class:'morphology-condition'}));
        appendVariants(slot,form,e,t,row.language);cell.append(slot);
      }
      if(group.axes){
        for(const value of group.rows){
          const tr=e('tr'),th=e('th',label(group.forms,group.axes[0],value),{scope:'row'});
          if(group.axes[0]==='case'&&row.paradigm_focus?.case===value){
            tr.classList.add('morphology-requested');tr.dataset.requestedCase=value;
            th.append(e('span',' · '+t('morphology_requested'),{class:'small'}));
          }
          tr.append(th);
          for(const column of group.columns){
            const cell=e('td'),observations=group.forms.filter(form=>same(feature(form)[group.axes[0]],value)&&same(feature(form)[group.axes[1]],column));
            if(observations.length)for(const form of observations)observation(cell,form);
            else cell.append(e('span','—',{'aria-label':t('morphology_no_captured_form')}));
            tr.append(cell);
          }
          body.append(tr);
        }
      }else for(const form of group.forms){const tr=e('tr'),cell=e('td');tr.append(e('th',form.title,{scope:'row'}));observation(cell,form);tr.append(cell);body.append(tr);}
      frame.append(table);content.append(frame);
    }
  }
  render();return disclosure;
}

export function frequencyPositionView(position){
  if(!position||position.version!=='sloleks-lexeme-frequency-position-v1'||position.population_unit!=='source_lexeme_occurrence'
    ||position.corpus!=='Gigafida 2.0'||position.inventory!=='Sloleks 3.1')return null;
  const p=position;
  if(![p.frequency,p.eligible_total,p.rank_min,p.rank_max].every(Number.isInteger)||p.frequency<0||p.rank_min<1||p.rank_min>p.rank_max||p.rank_max>p.eligible_total)return null;
  if(!Number.isFinite(p.percentile_low)||!Number.isFinite(p.percentile_high)||!Number.isFinite(p.percentile_mid)||p.percentile_low<0||p.percentile_low>p.percentile_mid||p.percentile_mid>p.percentile_high||p.percentile_high>100)return null;
  return {rank:p.rank_min===p.rank_max?String(p.rank_min):p.rank_min+'–'+p.rank_max,total:p.eligible_total,low:p.percentile_low,high:p.percentile_high,mid:p.percentile_mid,
    inventory:p.inventory,corpus:p.corpus};
}

export function appendFrequencyPosition(parent,position,{element:e,translate:t}){
  const view=frequencyPositionView(position);if(!view)return null;
  const rank=t('frequency_position_rank',{rank:view.rank,total:view.total});
  const wrap=e('div',null,{class:'frequency-position'});
  wrap.append(e('p',t('frequency_position_title'),{class:'small'}),
    e('p',view.inventory+' · '+view.corpus+' · '+rank,{class:'small muted'}));
  const scale=e('div',null,{class:'frequency-position-scale',role:'img',
    'aria-label':view.inventory+' · '+view.corpus+' · '+rank+'. '+t('frequency_position_range')});
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','-1 0 102 8');svg.setAttribute('preserveAspectRatio','none');
  svg.setAttribute('class','frequency-position-drawing');svg.setAttribute('aria-hidden','true');
  for(const x of [25,50,75]){
    const tick=document.createElementNS('http://www.w3.org/2000/svg','line');
    for(const [key,value]of Object.entries({x1:x,x2:x,y1:0,y2:8,class:'frequency-position-tick'}))tick.setAttribute(key,String(value));
    svg.append(tick);
  }
  const band=document.createElementNS('http://www.w3.org/2000/svg','rect');
  const attributes={x:view.low,y:0,width:view.high-view.low,height:8,class:'frequency-position-band'};
  for(const [key,value]of Object.entries(attributes))band.setAttribute(key,String(value));
  const marker=document.createElementNS('http://www.w3.org/2000/svg','line');
  for(const [key,value]of Object.entries({x1:view.mid,x2:view.mid,y1:0,y2:8,class:'frequency-position-point'}))marker.setAttribute(key,String(value));
  svg.append(band,marker);scale.append(svg);
  const ends=e('div',null,{class:'frequency-position-ends', 'aria-hidden':'true'});
  ends.append(e('span',t('frequency_less')),e('span',t('frequency_more')));wrap.append(scale,ends);parent.append(wrap);return wrap;
}
