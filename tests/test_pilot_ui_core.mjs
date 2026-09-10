import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {basePath,localePath,anchorForText,tierBlocks,usedBadges,safeSourceLink,sourceExcerptPresentation,readableLocator,errorKey} from '../pilot_ui/core.mjs';

test('prefix routes never introduce an external or traversal route',()=>{
  assert.equal(localePath('/tools/klikai','it'),'/tools/klikai/it/');assert.equal(localePath('','sl'),'/');assert.equal(basePath('/pilot/'),'/pilot');
  for(const value of ['//evil.test','/pilot/../private','https://evil.test','/x?query','/x%2fprivate'])assert.throws(()=>basePath(value));
});
test('Unicode anchors preserve emoji, combining marks and repeated exact words',()=>{
  const text='🙂 e\u0301 otrok otrok — Huga.';const start=text.lastIndexOf('otrok'),anchor=anchorForText('b1',text,start,start+5);
  assert.equal(anchor.start,Array.from(text.slice(0,start)).length);assert.equal(anchor.quote,'otrok');assert.equal(anchor.prefix,'🙂 e\u0301 otrok ');assert.equal(anchor.suffix,' — Huga.');
  assert.deepEqual(anchorForText('accent','e\u0301',0,2),{block_id:'accent',start:0,end:2,quote:'e\u0301',prefix:'',suffix:''});assert.equal(anchorForText('emoji','🙂',1,2),null);assert.equal(anchorForText('b','abc',1,1),null);
});
test('canonical blocks remain byte-for-byte text; missing identities are not invented',()=>{
  const b={block_id:'source-block',text:'Théophile <img onerror=x> [1]'};assert.deepEqual(tierBlocks({blocks:[b]}),[b]);assert.deepEqual(tierBlocks({short_answer:'Huga'}),[{block_id:null,text:'Huga'}]);assert.deepEqual(tierBlocks({answer_md:'Daljša razlaga'},'long'),[{block_id:null,text:'Daljša razlaga'}]);
});
test('used badges deduplicate actual source identities, not all retrieved sources',()=>{
  assert.deepEqual(usedBadges({source_badges:[{source_key:'sloleks'}, {source_key:'sloleks'},'epravopis'],evidence:[{source_key:'unused'}]}),[{source_key:'sloleks'},'epravopis']);assert.deepEqual(usedBadges({evidence:[{source_key:'unused'}]}),[]);
});
test('source links allow verified publisher HTTPS and reject active or internal URLs',()=>{
  assert.equal(safeSourceLink('https://svetovalnica.zrc-sazu.si/topic/7758'),'https://svetovalnica.zrc-sazu.si/topic/7758');
  for(const link of ['javascript:alert(1)','http://fran.si','https://fran.si.evil.test/x','https://user:pass@fran.si/x','http://127.0.0.1:18220','data:text/html,hello'])assert.equal(safeSourceLink(link),null);
});
test('both locales have every static UI key and an identical nonempty dictionary',()=>{
  const sl=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/sl.json',import.meta.url))),it=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/it.json',import.meta.url)));
  assert.deepEqual(Object.keys(sl).sort(),Object.keys(it).sort());assert(Object.keys(sl).length>=205);for(const [k,v]of Object.entries(sl)){assert.equal(typeof v,'string',k);assert(v.trim());assert(it[k].trim());}
  const html=fs.readFileSync(new URL('../pilot_ui/index.html',import.meta.url),'utf8');for(const m of html.matchAll(/data-i18n(?:-aria|-placeholder)?="([a-z_]+)"/g)){assert(sl[m[1]],m[1]);assert(it[m[1]],m[1]);}
  assert.equal((html.match(/id="question"/g)||[]).length,1);assert(!/<script(?![^>]*src=)[^>]*>/i.test(html));
});
test('public browser code has no browser storage, raw model endpoint or unsafe HTML sink',()=>{
  const code=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');assert(!/\.innerHTML\s*=|insertAdjacentHTML|localStorage|sessionStorage|127\.0\.0\.1|localhost|18221|8021|eval\(/.test(code));assert(code.includes("credentials:'same-origin'"));assert(code.includes("headers['X-CSRF-Token']"));
});
test('unrecognized API internals use generic translated error',()=>{
  assert.equal(errorKey('database_private_path_leak'),'request_failed');assert.equal(errorKey('invalid_credentials'),'login_failed');assert.equal(errorKey('generation_rejected'),'answer_guard_failed');
});

test('human source locators show the source page and omit internal coordinate arrays',()=>{
  assert.equal(readableLocator({page_label:'192',page_index:191,anchors:[{bbox:[1,2,3,4]}],coordinate_system:'internal'},{page:'Pagina',topic:'Discussione'}),'Pagina 192');
  assert.equal(readableLocator({page_index:0},{page:'Stran',topic:'Tema'}),'Stran 1');
  assert.equal(readableLocator({topic_id:7758},{page:'Stran',topic:'Tema'}),'Tema 7758');
  assert.equal(readableLocator({anchors:[{text:'hidden'}],sha256:'private'},{page:'Page',topic:'Topic'}),'');
});

const signatureFixture = excerpt => ({excerpt,url:'https://svetovalnica.zrc-sazu.si/topic/7758',bibliography:{authors:['Avtor Primer'],signed_date_text:['junij 2026']}});
test('source drawer moves only metadata-matched terminal signature to deliberate original detail',()=>{
  const prose='Victor Hugo je predmet primera. 🙂 e\u0301\nIme Avtor Primer je omenjeno v povedi.';
  const footer='Odgovor je bil pripravljen v sklopu projekta Preizkus (2026), ki ga financira Ministrstvo za kulturo Republike Slovenije.';
  for (const ending of ['','\n'+footer]) {
    const data=signatureFixture(prose+'\nAvtor Primer (junij 2026)'+ending),before=JSON.stringify(data),result=sourceExcerptPresentation(data);
    assert.equal(result.text,prose);assert.equal(result.original,data.excerpt);assert.equal(result.attributionHidden,true);assert.equal(JSON.stringify(data),before);
  }
  const crlf=signatureFixture('Izvirni zapis.\r\nAvtor Primer (junij 2026)\r\n');
  assert.equal(sourceExcerptPresentation(crlf).text,'Izvirni zapis.');assert.equal(sourceExcerptPresentation(crlf).original,crlf.excerpt);
});
test('source drawer preserves inline names, subject names, quotes, unknown dates and following prose',()=>{
  for(const text of ['Victor Hugo','Besedilo o Avtor Primer.','Besedilo.\nAvtor Primer','Besedilo.\nDruga Oseba (junij 2026)','Besedilo.\nAvtor Primer (maj 2026)','Avtor Primer (junij 2026)','Besedilo.\n> Avtor Primer (junij 2026)','Besedilo.\n»Avtor Primer (junij 2026)«','Besedilo. Avtor Primer (junij 2026)','Besedilo.\nAvtor Primer (junij 2026)\nNaslednja vsebinska poved.']) {
    const result=sourceExcerptPresentation(signatureFixture(text));assert.equal(result.text,text);assert.equal(result.attributionHidden,false,text);
  }
});
test('source drawer requires explicit structured signature metadata from the original publisher',()=>{
  const text='Besedilo.\nAvtor Primer (junij 2026)';
  for(const overrides of [{bibliography:'Avtor Primer (junij 2026)'},{bibliography:{authors:['Avtor Primer'],date:'2026-06-01'}},{bibliography:{authors:['Druga Oseba'],signed_date_text:['junij 2026']}},{url:'https://fran.si/'},{url:'https://svetovalnica.zrc-sazu.si.evil.test/'}]) {
    const data={...signatureFixture(text),...overrides};assert.equal(sourceExcerptPresentation(data).text,text);
  }
});
test('answer blocks and feedback offsets retain source-like bylines unchanged',()=>{
  const text='Izvirni odgovor.\nAvtor Primer (junij 2026)',block={block_id:'canonical-source-fallback',text};
  assert.deepEqual(tierBlocks({blocks:[block]},'long'),[block]);
  const start=text.indexOf('Avtor');assert.equal(anchorForText(block.block_id,text,start,text.length).quote,'Avtor Primer (junij 2026)');
});
