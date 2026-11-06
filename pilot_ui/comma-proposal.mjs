// Saved tool observations only. Rendering and copying never contact the checker.
export function commaConsentFields(capabilities, checked) {
  return capabilities?.comma_checker?.configured === true
    ? {comma_checker_consent:checked === true} : {};
}

export function commaTermsLink(value) {
  try {
    const url=new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password &&
      ['orodja.cjvt.si','cjvt.si','www.cjvt.si'].includes(url.hostname.toLowerCase())
      ? url.href : null;
  } catch {return null;}
}

export function commaProposalView(proposal) {
  if (!proposal || typeof proposal !== 'object') return null;
  const original=typeof proposal.original_text === 'string' ? proposal.original_text : '';
  const view={status:proposal.status,original,partial:proposal.checked_extent?.complete === false};
  if (proposal.status !== 'ok') return view;
  if (proposal.offset_convention !== 'unicode_codepoint_half_open' ||
      typeof proposal.proposed_text !== 'string' || !Array.isArray(proposal.edits))
    return {...view,status:'alignment_failed'};
  const chars=Array.from(original),parts=[];
  const edits=proposal.edits.map((edit,index)=>({...edit,index})).sort((a,b)=>a.start-b.start || a.end-b.end);
  let cursor=0;
  for (const edit of edits) {
    if (!Number.isInteger(edit.start) || !Number.isInteger(edit.end) ||
        edit.start < cursor || edit.end < edit.start || edit.end > chars.length ||
        typeof edit.replacement !== 'string' || !['insert','remove'].includes(edit.kind) ||
        !/^[,\s]*$/u.test(chars.slice(edit.start,edit.end).join('')) ||
        !/^[,\s]*$/u.test(edit.replacement))
      return {...view,status:'alignment_failed'};
    if (cursor < edit.start) parts.push({kind:'same',text:chars.slice(cursor,edit.start).join('')});
    if (edit.start < edit.end) parts.push({kind:'remove',text:chars.slice(edit.start,edit.end).join(''),editIndex:edit.index});
    if (edit.replacement) parts.push({kind:'insert',text:edit.replacement,editIndex:edit.index});
    cursor=edit.end;
  }
  parts.push({kind:'same',text:chars.slice(cursor).join('')});
  const proposed=parts.filter(part=>part.kind !== 'remove').map(part=>part.text).join('');
  if (proposed !== proposal.proposed_text) return {...view,status:'alignment_failed'};
  const commas=kind=>parts.filter(part=>part.kind === kind)
    .reduce((count,part)=>count+part.text.split(',').length-1,0);
  return {...view,proposed,parts,changed:proposed !== original,added:commas('insert'),removed:commas('remove')};
}

export function commaExplanationLinks(proposal, blocks=[], citations=[]) {
  if (!Array.isArray(proposal?.explanation_links) || !Array.isArray(proposal.edits)) return [];
  const relations=new Set(['supports_proposal','disagrees_with_proposal','conditional_support']);
  const seen=new Set();
  return proposal.explanation_links.filter(link=>{
    if (!link || !Number.isInteger(link.edit_index)) return false;
    const edit=proposal.edits[link.edit_index];
    const block=blocks.find(row=>row.block_id===link.block_id && typeof row.text==='string' && row.text.trim());
    if (!edit || !block || link.edit_start!==edit.start || link.edit_end!==edit.end ||
        !relations.has(link.relation) || !['claim_id','block_id','unit_id'].every(key=>
          typeof link[key]==='string' && link[key].length>0) ||
        !Array.isArray(link.citation_indexes) || !link.citation_indexes.length ||
        !Array.isArray(link.evidence_ids) || !link.evidence_ids.length) return false;
    const bound=link.citation_indexes.map(index=>Number.isInteger(index) && index>=0 ? citations[index] : null);
    if (bound.some(row=>!row || row.block_id!==link.block_id || row.claim_id!==link.claim_id ||
        row.unit_id && row.unit_id!==link.unit_id)) return false;
    const evidence=bound.map(row=>row.evidence_id || row.citation_id);
    if (link.evidence_ids.some(id=>typeof id!=='string' || !evidence.includes(id)) ||
        evidence.some(id=>!link.evidence_ids.includes(id))) return false;
    const identity=JSON.stringify([link.edit_index,link.block_id,link.claim_id,link.relation]);
    if (seen.has(identity)) return false;
    seen.add(identity);return true;
  });
}

export function commaExplanationHeading(proposal, citations) {
  if (!proposal || !Array.isArray(citations) || !citations.length) return null;
  return proposal.explanation_status==='related_guidance'
    ? 'comma_related_guidance_heading' : 'comma_explanation_heading';
}

const statuses=new Set(['consent_required','target_required','too_long','unavailable',
  'timeout','alignment_failed','malformed_response','unsupported_language']);

export function renderCommaProposal(root, proposal, {element,translate,copyText,
    blocks=[],citations=[],openExplanation=()=>{}}) {
  root.replaceChildren();
  const view=commaProposalView(proposal);
  root.hidden=!view;
  if (!view) return;
  const t=translate,e=element;
  root.append(e('h3',t('comma_proposal_title')));
  if (view.original) root.append(e('h4',t('comma_original')),
    e('div',view.original,{class:'comma-text comma-original'}));
  if (view.status !== 'ok') {
    root.append(e('p',t('comma_status_'+(statuses.has(view.status) ? view.status : 'unknown'),{limit:proposal.application_limit_characters||3000}),
      {class:'qualified-note',role:'status'}));
    return;
  }
  root.append(e('p',t('comma_prediction_notice'),{class:'small muted'}),
    e('h4',t('comma_proposed')),e('div',view.proposed,{class:'comma-text comma-proposed'}));
  if (view.partial) root.append(e('p',t('comma_partial'),{class:'qualified-note'}));
  if (view.changed) {
    root.append(e('h4',t('comma_changes')),
      e('p',t('comma_change_summary',{added:view.added,removed:view.removed}),{class:'small muted'}));
    const changes=e('div',null,{class:'comma-text comma-diff'});
    for (const part of view.parts) changes.append(e(part.kind === 'same' ? 'span' :
      part.kind === 'insert' ? 'ins' : 'del',part.text,part.kind === 'same' ? {} :
        {'aria-label':t(part.kind === 'insert' ? 'comma_added' : 'comma_removed'),
          title:t('comma_change_number',{number:part.editIndex+1}),
          'data-comma-edit-index':part.editIndex}));
    root.append(changes);
    const links=commaExplanationLinks(proposal,blocks,citations);
    if (links.length) {
      const navigation=e('div',null,{class:'comma-explanation-links'});
      for (const link of links) {
        const button=e('button',t('comma_link_'+link.relation,{number:link.edit_index+1}),
          {type:'button',class:'plain comma-explanation-link','data-comma-edit-index':link.edit_index});
        button.addEventListener('click',()=>openExplanation(link));navigation.append(button);
      }
      root.append(navigation);
      if (new Set(links.map(link=>link.edit_index)).size<proposal.edits.length)
        root.append(e('p',t('comma_partial_explanation'),{class:'small muted'}));
    }
  } else root.append(e('p',t('comma_no_changes'),{class:'qualified-note',role:'status'}));
  const button=e('button',t('comma_copy'),{type:'button',class:'plain comma-copy'});
  const status=e('p','',{class:'small muted',role:'status','aria-live':'polite'});
  button.addEventListener('click',async()=>{
    try {await copyText(view.proposed);status.textContent=t('copied');}
    catch {status.textContent=t('copy_failed');}
  });
  root.append(button,status);
}
