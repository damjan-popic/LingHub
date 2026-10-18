// A supported excerpt is useful delivery, but is never labeled generation.
export function hasSupportedAnswer(tier) {
  return ['generated','mixed'].includes(tier?.answer_kind) ||
    ['generated','source_supported_partial','mixed_generated_and_source_partial'].includes(tier?.generation_status);
}

export function noAnswerFeedback(tier) {
  // Lack of evidence is distinct from unavailable services or rejected prose.
  return tier?.generation?.status === 'no_support' ||
    tier?.status === 'no_hit' && tier?.generation_status === 'not_generated';
}

export function basePath(value) {
  if (!value || value === '/') return '';
  if (!/^\/[A-Za-z0-9_/-]+$/.test(value) || value.includes('//') || value.split('/').includes('..')) throw Error('Invalid application prefix');
  return value.replace(/\/$/, '');
}
export function localePath(base, locale) { return basePath(base) + (locale === 'it' ? '/it/' : '/'); }
export function codePointSlice(text, start, end) { return Array.from(text).slice(start, end).join(''); }
export function anchorForText(blockId, text, startUtf16, endUtf16) {
  if (!Number.isInteger(startUtf16) || !Number.isInteger(endUtf16) || startUtf16 < 0 || endUtf16 <= startUtf16 || endUtf16 > text.length) return null;
  // Reject offsets splitting a surrogate pair; combining marks remain distinct code points.
  const split = n => n > 0 && n < text.length && /[\uD800-\uDBFF]/.test(text[n - 1]) && /[\uDC00-\uDFFF]/.test(text[n]);
  if (split(startUtf16) || split(endUtf16)) return null;
  const start = Array.from(text.slice(0, startUtf16)).length, end = Array.from(text.slice(0, endUtf16)).length;
  return {block_id:blockId,start,end,quote:codePointSlice(text,start,end),prefix:codePointSlice(text,Math.max(0,start-32),start),suffix:codePointSlice(text,end,end+32)};
}
export function tierBlocks(tier, kind = 'short') {
  if (Array.isArray(tier?.blocks) && tier.blocks.length) return tier.blocks.filter(b => typeof b.block_id === 'string' && typeof b.text === 'string');
  const value = kind === 'short' ? tier?.short_answer : tier?.answer_md;
  // Legacy text can be displayed, but does not acquire invented feedback block identities.
  return typeof value === 'string' && value ? [{block_id:null,text:value}] : [];
}
export function paragraphGroups(blocks) {
  // Older captures retain their paragraph-per-block layout. A new paragraph
  // groups only adjacent fragments; it never unions their citation scopes.
  const groups=[];
  for (const block of blocks) {
    const id=typeof block.paragraph_id==='string' && /^P(?:[1-9]|1[0-4])$/.test(block.paragraph_id) ? block.paragraph_id : null;
    const last=groups.at(-1);
    if(id && last?.id===id) last.blocks.push(block);
    else groups.push({id,blocks:[block]});
  }
  return groups;
}
export function isCpuTask(task) {
  return ['source_search','text_check','terminology','morphology','jezikovna_urgenca','jezikovna_posta'].includes(task);
}
export function modelChoiceKey(model) {
  return {auto:'model_auto',qwen:'model_qwen',gemma:'model_gemma'}[model] || 'model';
}
export function modelAvailabilityKey(observed) {
  if (!observed || typeof observed.available !== 'boolean') return 'model_readiness_unknown';
  if (observed.status === 'CPU_LOADING') return 'model_loading';
  if (observed.status === 'CPU_BUSY') return 'model_queue_busy';
  if (!observed.available) return 'model_not_available';
  if (observed.status === 'SHARED_READY' || observed.provider_scope === 'shared' && observed.device === 'gpu') return 'model_shared_ready';
  return observed.device === 'cpu' ? observed.cpu?.loaded === false && observed.cpu?.availability === 'lazy' ? 'model_cpu_lazy' : 'model_cpu_ready' : observed.device === 'gpu' ? 'model_own_gpu_ready' : 'model_ready';
}
export function savedProvider(tier) {
  // A reopened answer uses its saved result, never today's readiness/routing.
  const provider=tier?.generation?.provider || {}, identity=tier?.model_identity || {}, execution=tier?.execution || {};
  const actual=provider.repository || provider.model_id || identity.repository || identity.model_id || '';
  const device=provider.device || execution.device, scope=provider.provider_scope || execution.provider_scope;
  const backend=device === 'gpu' ? scope === 'shared' ? 'backend_shared_gpu' : scope === 'own' ? 'backend_own_gpu' : 'backend_gpu' : device === 'cpu' ? 'backend_cpu' : null;
  const reason=provider.route_reason || execution.route_reason;
  const reasons=new Set(['shared_ready','shared_busy_own','shared_unavailable_own','shared_incompatible_own','shared_terminal_failure_own','own_gpu_ready','own_cpu_fallback']);
  return {model:typeof actual === 'string' ? actual : '',backend,reason:reasons.has(reason) ? 'route_'+reason : null};
}
export function progressLabel(job, task, tier='short') {
  if (jobState(job) === 'cancelled') return 'cancelled';
  if (job?.cancel_requested === true && pendingStates.has(jobState(job))) return job.cancellation_mode === 'stop_before_generation' ? 'cancelling_before_generation' : 'cancelling_current_operation';
  if (jobState(job) === 'queued') return 'queued';
  if (job?.progress?.stage === 'retrieval') return 'retrieving_sources';
  if (job?.progress?.stage === 'generation') return 'generating_grounded';
  if (job?.progress?.stage === 'validation' || jobState(job) === 'validating') return 'validating';
  if (['loading_model','waiting_model'].includes(jobState(job))) return 'loading_model';
  return isCpuTask(task) ? 'deterministic_working' : tier === 'long' ? 'generating_long' : 'generating_short';
}
export function settledMatchUsage(items, family) {
  if (['short','long'].some(tier => family?.[tier] && pendingStates.has(jobState(family[tier])))) return items;
  const used = new Set(['short','long'].flatMap(tier => family?.[tier]?.state === 'complete' ? family[tier].evidence || [] : [])
    .map(item => item.evidence_id || item.citation_id));
  return items.map(item => ({...item,used_in_answer:used.has(item.evidence_id)}));
}
export function matchUseLabel(item, pending) {
  return pending ? 'match_pending' : item.used_in_answer === true ? 'match_used' : 'match_other';
}
export function matchGroups(items, source='') {
  const groups = new Map(), seen = new Set();
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item.evidence_id !== 'string' || !item.evidence_id ||
        typeof item.source_slug !== 'string' || !item.source_slug ||
        typeof item.title !== 'string' || typeof item.snippet !== 'string' ||
        (source && item.source_slug !== source) || seen.has(item.evidence_id)) continue;
    seen.add(item.evidence_id);
    if (!groups.has(item.source_slug)) groups.set(item.source_slug, []);
    groups.get(item.source_slug).push(item);
  }
  return [...groups].map(([source_slug, records]) => ({source_slug, records}));
}
export function generationNotice(tier) {
  const status = tier?.generation?.status;
  if (tier?.generation_status === 'source_supported_partial') return 'generation_source_excerpt';
  if (tier?.generation?.error_code === 'ambiguous_remote_generation') return 'generation_outcome_unknown';
  if (status === 'generated') return 'generated_grounded';
  if (status === 'failed' || status === 'unavailable') return 'generation_fallback';
  if (status === 'no_support') return 'generation_no_support';
  return null;
}
export function sourceStatusNotes(source) {
  // These are views of saved metadata, not new statements about a source's
  // present authority. A migration/upload date cannot replace a signed date.
  const notes=[],bib=source?.bibliographic || source?.bibliography,status=source?.source_status || bib?.source_status || source?.status;
  if(typeof status==='string' && /osnutek|draft|bozza|predlog|proposal|proposta/i.test(status)) {
    const proposal=/predlog|proposal|proposta/i.test(status),bare=/^(draft|osnutek|bozza|predlog|proposal|proposta)$/i.test(status.trim());
    notes.push({category:proposal?'proposal_status':'draft_status',key:bare?proposal?'source_proposal_note':'source_draft_note':'source_status_note',values:bare?{}:{status:status.trim()}});
  }
  else if(typeof status==='string' && ['current_reference','status_notice'].includes(bib?.normative_status))notes.push({category:'source_status',key:'source_status_note',values:{status:status.trim()}});
  if(source?.annual_scope || source?.publication_scope==='annual_additions')notes.push({category:'source_scope',key:'source_annual_note',values:{}});
  if(typeof bib?.coverage_note==='string' && bib.coverage_note.trim())notes.push({category:'source_scope',key:'source_coverage_note',values:{scope:bib.coverage_note.trim()}});
  if(source?.publication_scope==='individual_advisory_answer' && Array.isArray(bib?.signed_date_text)) {
    const dates=[...new Set(bib.signed_date_text.filter(value=>typeof value==='string' && value.trim()).map(value=>value.trim()))];
    if(dates.length)notes.push({category:'source_date',key:'source_signed_date_note',values:{date:dates.join('; ')}});
  }
  return notes;
}
export function canOpenCurrentOriginal(source) {
  return source?.current_original_lookup===true && source?.original_view==='historical_unavailable';
}
export function answerNotes(tier) {
  const notes=[],blocks=tierBlocks(tier),evidence=Array.isArray(tier?.evidence)?tier.evidence:[];
  const eid=item=>item?.evidence_id || item?.citation_id || item?.passage_id;
  const limitationKeys={partial_evidence:'limitation_partial_evidence',no_answer_evidence:'limitation_no_answer_evidence',
    conflicting_evidence:'limitation_conflicting_evidence',source_scope:'limitation_source_scope',proposal_status:'source_proposal_note',
    uncertain_entry_boundary:'limitation_uncertain_entry_boundary',unsupported_inference:'limitation_unsupported_inference'};
  const add=note=>{const same=notes.find(old=>old.text===note.text && old.key===note.key && JSON.stringify(old.values)===JSON.stringify(note.values));
    if(same){for(const item of note.evidence)if(!same.evidence.some(old=>eid(old)===eid(item)))same.evidence.push(item);}
    else notes.push(note);};
  for(const value of Array.isArray(tier?.limitations)?tier.limitations:[]) {
    const text=typeof value==='string'?value:typeof value?.message==='string'?value.message:null;
    if(text && blocks.some(block=>block.text.trim()===text.trim()))continue;
    if(typeof value==='object' && value?.scope==='claim' && (!Array.isArray(value.claim_ids) || !value.claim_ids.some(id=>blocks.some(block=>block.block_id===id))))continue;
    if(!text && !limitationKeys[value?.code])continue;
    const ids=new Set(blocks.filter(block=>value?.scope!=='claim' || value.claim_ids.includes(block.block_id)).flatMap(block=>block.evidence_ids || []));
    add({category:value?.code || 'limitation',...(text?{text}:{key:limitationKeys[value.code],values:{}}),evidence:evidence.filter(item=>ids.has(eid(item)))});
  }
  if(hasSupportedAnswer(tier)) {
    const seen=new Set();
    for(const item of evidence) {
      // Multiple passages from one record share a status/date. Do not repeat
      // the same record's note or turn incidental search hits into support.
      const record=JSON.stringify([item.source_slug,item.external_id || eid(item),item.source_revision]);
      if(seen.has(record))continue;seen.add(record);
      for(const note of sourceStatusNotes(item)) {
        if(notes.some(old=>old.category===note.category && old.evidence.some(ref=>ref===item || ref.source_slug===item.source_slug && ref.external_id && ref.external_id===item.external_id)))continue;
        add({...note,evidence:[item]});
      }
    }
  }
  return notes;
}
export function blockTextPresentation(tier, block) {
  const evidence = Array.isArray(tier?.evidence) ? tier.evidence : [];
  if (tier?.deterministic_result?.task === 'editorial_search') {
    // Source text can contain its own bracket references. The application link
    // belongs outside the canonical block and is bound to the record index.
    const match = /^editorial-record-(0|[1-9]\d*)$/.exec(block.block_id || '');
    const index = match ? Number(match[1]) : -1;
    const item = Number.isSafeInteger(index) && index >= 0 ? evidence[index] : null;
    return {parts:[{text:block.text}],separateCitation:item ? {text:'['+(index+1)+']',number:index+1,evidence:item} : null};
  }
  const parts = [];
  let end = 0;
  for (const match of block.text.matchAll(/\[(\d{1,3})\]/g)) {
    parts.push({text:block.text.slice(end,match.index)});
    const item = evidence.find((e,i) => Number(e.local_number || e.number || i+1) === Number(match[1]));
    parts.push(item ? {text:match[0],number:match[1],evidence:item} : {text:match[0]});
    end = match.index + match[0].length;
  }
  parts.push({text:block.text.slice(end)});
  if (hasSupportedAnswer(tier) && Array.isArray(block.evidence_ids)) {
    const inline = new Set(parts.filter(part=>part.evidence).map(part=>part.evidence.evidence_id));
    const selected = [...new Set(block.evidence_ids)].map(id=>evidence.find(item=>item.evidence_id===id))
      .filter(item=>item && !inline.has(item.evidence_id) && Number.isInteger(item.local_number) && item.local_number>0);
    return {parts,separateCitation:null,separateCitations:selected.map(item=>({text:'['+item.local_number+']',number:item.local_number,evidence:item}))};
  }
  return {parts,separateCitation:null};
}
export function sourceKey(item) { return typeof item === 'string' ? item : item?.source_key || item?.source_slug || item?.key || item?.slug || ''; }
export function usedBadges(tier) {
  const seen = new Set();
  return (tier?.source_badges || []).filter(item => {const key = sourceKey(item); if (!key || seen.has(key)) return false; seen.add(key); return true;});
}
export function safeSourceLink(value) {
  try { const url = new URL(value); if (url.protocol !== 'https:' || url.username || url.password) return null;
    const host = url.hostname.toLowerCase();
    if (!['fran.si','svetovalnica.zrc-sazu.si','www.fran.si','www.jeziknaklik.it','jeziknaklik.it','viri.cjvt.si','www.clarin.si','clarin.si','www.slori.org','slori.org'].includes(host) && !host.endsWith('.jeziknaklik.it') && !host.endsWith('.zrc-sazu.si')) return null;
    return url.href;
  } catch { return null; }
}
export function sourceExcerptPresentation(data) {
  const original = data?.excerpt || data?.text || '';
  const unchanged = {text:original,original,attributionHidden:false};
  if (typeof original !== 'string' || !original) return unchanged;
  // This is a view over the original excerpt, never a rewrite of source/answer
  // blocks or their canonical offsets. Only explicit publisher metadata may
  // identify a signature; names inferred from prose or titles are not enough.
  const link = safeSourceLink(data.url || data.original_url);
  if (!link || new URL(link).hostname !== 'svetovalnica.zrc-sazu.si') return unchanged;
  const bibliography = data.bibliography;
  if (!bibliography || typeof bibliography !== 'object') return unchanged;
  const authors = Array.isArray(bibliography.authors) ? bibliography.authors : [];
  const dates = Array.isArray(bibliography.signed_date_text) ? bibliography.signed_date_text : [];
  const signatures = new Set();
  for (const author of authors) for (const date of dates) {
    if (typeof author === 'string' && author.trim() && !/[\r\n]/.test(author) &&
        typeof date === 'string' && date.trim() && !/[\r\n]/.test(date)) signatures.add(author.trim()+' ('+date.trim()+')');
  }
  // A recognized administrative funding footer can follow the signature.
  // Unknown following prose prevents hiding, including apparent quotations.
  const fundingFooter = /^Odgovor je bil pripravljen v sklopu projekta [^\r\n]+, ki ga financira Ministrstvo za kulturo Republike Slovenije\.$/;
  for (const match of original.matchAll(/^[^\r\n]+/gm)) {
    if (!signatures.has(match[0].trim()) || !original.slice(0,match.index).trim()) continue;
    const suffix = original.slice(match.index+match[0].length).trim();
    if (suffix && !fundingFooter.test(suffix)) continue;
    return {text:original.slice(0,match.index).trimEnd(),original,attributionHidden:true};
  }
  return unchanged;
}
export function jobState(value) { return value?.state === 'complete' ? value.status || 'complete' : value?.state || value?.status || 'queued'; }
export function readableLocator(locator, labels) {
  if (typeof locator === 'string') return locator;
  if (!locator || typeof locator !== 'object') return '';
  const parts=[];
  if (typeof locator.page_label === 'string' || Number.isInteger(locator.page_label)) parts.push(labels.page+' '+locator.page_label);
  else if (Number.isInteger(locator.page_index) && locator.page_index>=0) parts.push(labels.page+' '+(locator.page_index+1));
  if (typeof locator.headword === 'string') parts.push(locator.headword);
  if (typeof locator.section === 'string') parts.push(locator.section);
  if (typeof locator.topic_id === 'string' || Number.isInteger(locator.topic_id)) parts.push(labels.topic+' '+locator.topic_id);
  return parts.join(' · ');
}
export const pendingStates = new Set(['queued','running','retrieving','searching','generating','validating','loading_model','waiting_model','deferred']);
export const acceptedStates = new Set(['validated','ready','complete','completed','ok','no_hit','no_answer','partial','source_only']);
export function errorKey(code) {
  const map = {generation_waiting_capacity:'generation_waiting_capacity',input_language_required:'input_language_required',invalid_credentials:'login_failed',login_failed:'login_failed',login_throttled:'login_throttled',rate_limited:'login_throttled',unauthenticated:'session_expired',session_expired:'session_expired',password_change_required:'password_required',model_unavailable:'unavailable_model',unsupported_task:'unsupported_task',generation_rejected:'answer_guard_failed',validation_failed:'answer_guard_failed',snapshot_unavailable:'source_unavailable',not_found:'source_unavailable',queue_full:'queued',input_too_long:'input_too_long',password_mismatch:'password_mismatch'};
  return map[code] || 'request_failed';
}
