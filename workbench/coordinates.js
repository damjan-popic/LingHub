// Browser selection indexes are UTF-16; API coordinates count Unicode code points.
export function toCodePoints(text, utf16) {
  if (!Number.isInteger(utf16) || utf16 < 0 || utf16 > text.length) throw new Error('Neveljaven izbor.');
  if (utf16 && utf16 < text.length && /[\uD800-\uDBFF]/.test(text[utf16-1]) && /[\uDC00-\uDFFF]/.test(text[utf16])) throw new Error('Izbor deli znak Unicode.');
  return Array.from(text.slice(0, utf16)).length;
}
export function toUtf16(text, codePoints) {
  const chars=Array.from(text);
  if (!Number.isInteger(codePoints) || codePoints < 0 || codePoints > chars.length) throw new Error('Neveljaven odmik.');
  return chars.slice(0,codePoints).join('').length;
}
export function exactSlice(text,start,end) {return Array.from(text).slice(start,end).join('');}
export function alignSpans(left,right) {
  const rows=[],seen=new Set();
  for(const a of left){const matches=right.filter(b=>a.start<b.end&&b.start<a.end);matches.forEach(b=>seen.add(b));rows.push({left:[a],right:matches,mapping:matches.length===1&&a.start===matches[0].start&&a.end===matches[0].end?'exact':matches.length?'split_merge':'unmatched'});}
  for(const b of right)if(!seen.has(b))rows.push({left:[],right:[b],mapping:'unmatched'});
  return rows;
}
