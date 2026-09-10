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
  const map = {invalid_credentials:'login_failed',login_failed:'login_failed',login_throttled:'login_throttled',rate_limited:'login_throttled',unauthenticated:'session_expired',session_expired:'session_expired',password_change_required:'password_required',model_unavailable:'unavailable_model',unsupported_task:'unsupported_task',generation_rejected:'answer_guard_failed',validation_failed:'answer_guard_failed',snapshot_unavailable:'source_unavailable',not_found:'source_unavailable',queue_full:'queued',input_too_long:'input_too_long',password_mismatch:'password_mismatch'};
  return map[code] || 'request_failed';
}
