import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {safeSourceLink} from '../pilot_ui/core.mjs';
import {appendMorphologyTables,appendFrequencyPosition} from '../pilot_ui/morphology-tables.mjs';
const messages=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/sl.json',import.meta.url)));
const t=(key,values={})=>(messages[key]||key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''));
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.dataset={};this.hidden=false;this.textContent='';this.classList={add:()=>{}};}
 setAttribute(k,v){this.attributes[k]=v;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){this.children=[];}
 after(node){this.next=node;}
 addEventListener(){}
}
const descendants=(node,tag)=>node.children?.flatMap(child=>typeof child==='object'?[...(child.tag===tag?[child]:[]),...descendants(child,tag)]:[])||[];
const text=node=>typeof node==='string'?node:(node.textContent||'')+(node.children||[]).map(text).join('');
const source=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
const renderer=source.slice(source.indexOf('function renderLexical('),source.indexOf('function renderFamily('));
const el=source.split('\n').find(line=>line.startsWith('function el('));
for(const id of ['word','phrase','variants','translation'])test('complete captured '+id+' cards preserve sections and expandable details',{skip:!process.env.KLIKAI_QUALITY_PROFILES},()=>{
 const cards=JSON.parse(fs.readFileSync(process.env.KLIKAI_QUALITY_PROFILES+'/'+id+'.cards.private.json'));
 const root=new Node('section');const context={document:{createElement:t=>new Node(t)},state:{locale:'sl'},$:()=>root,clear:n=>n.replaceChildren(),t,appendMorphologyTables,appendFrequencyPosition,family:{lexical_profile:cards}};
 vm.runInNewContext(el+'\n'+renderer+'\nrenderLexical(family);',context);
 assert.equal(root.hidden,false);const sections=root.children.filter(n=>n.tag==='details');assert.equal(sections.length,cards.sections.length);
 for(let i=0;i<sections.length;i++){
  const articles=sections[i].children.filter(n=>n.tag==='article');assert.equal(articles.length,cards.sections[i].items.length);
  for(let j=0;j<articles.length;j++){
   const row=cards.sections[i].items[j];const article=articles[j];assert.equal(article.children[0].textContent,row.title);
   if(row.details?.length)assert(article.children.some(n=>n.tag==='details'&&n.children[0].textContent==='Podrobnosti vira'));
   if(row.forms?.length)assert(article.children.some(n=>n.tag==='details'&&n.children[0].textContent==='Pregibanje'&&descendants(n,'table').length));
  }
 }
});

test('one complete paradigm and a safe source reference, including old pattern-bearing cards',()=>{
 const root=new Node('section');const row={title:'Recorded name',text:'Relevant reading',label:'Sloleks',forms:[{title:'rodilnik',text:'A'}],patterns:[{title:'Old repeated view',items:[{title:'rodilnik',text:'A'}]}],reference_url:'https://www.smejse.it/reference.pdf'};
 const context={document:{createElement:t=>new Node(t)},state:{locale:'sl'},$:()=>root,clear:n=>n.replaceChildren(),safeSourceLink,t,appendMorphologyTables,appendFrequencyPosition,family:{lexical_profile:{title:'Podatki o izrazu',sections:[{title:'Oblike',items:[row]}]}}};
 vm.runInNewContext(el+'\n'+renderer+'\nrenderLexical(family);',context);
 const article=root.children.find(n=>n.tag==='details').children.find(n=>n.tag==='article');
 assert.equal(article.children.filter(n=>n.tag==='details').length,1);
 const link=article.children.find(n=>n.tag==='a');assert.equal(link.attributes.href,row.reference_url);assert.equal(link.attributes.rel,'noopener noreferrer');
 row.reference_url='javascript:alert(1)';vm.runInNewContext('renderLexical(family);',context);
 assert.equal(root.children.find(n=>n.tag==='details').children.find(n=>n.tag==='article').children.some(n=>n.tag==='a'),false);
 row.forms=[{title:'rodilnik',text:'A / B / C',pattern_memberships:[{title:'Vzorec 1',forms:['A']},{title:'Vzorec 2',forms:['B']}]}];
 vm.runInNewContext('renderLexical(family);',context);
 const full=root.children.find(n=>n.tag==='details').children.find(n=>n.tag==='article').children.find(n=>n.tag==='details');
 assert.equal(full.children[0].textContent,'Pregibanje');
 assert.equal(descendants(full,'table').length,1);
 assert.equal(descendants(full,'th').find(node=>node.attributes.scope==='row').textContent,'rodilnik');
 const cell=descendants(full,'td')[0];assert.equal(text(cell),'A / B / CVzorec 1: AVzorec 2: B');
});

test('captured frequency availability note remains visible without a fabricated lexical entry',()=>{
 const root=new Node('section'),answer=new Node('div');answer.textContent='Frozen LORIS answer[1]';
 const note='Za izraz »unmatched source expression« v zajetem Sloleksu 3.1 ni primerljivega podatka o pogostnosti v Gigafidi 2.0.';
 const family={lexical_profile:{title:'Podatki o izrazu',note:'Podatki iz virov.',sections:[],availability_notes:[note]}};
 const context={document:{createElement:t=>new Node(t)},state:{locale:'sl'},$:id=>id==='short-answer'?answer:root,clear:n=>n.replaceChildren(),safeSourceLink,t,appendMorphologyTables,appendFrequencyPosition,family};
 vm.runInNewContext(el+'\n'+renderer+'\nrenderLexical(family);',context);
 assert.equal(root.hidden,false);assert(text(root).includes(note));
 assert.equal(descendants(root,'article').length,0);assert.equal(descendants(root,'svg').length,0);
 assert.equal(descendants(root,'a').length,0);assert.equal(answer.textContent,'Frozen LORIS answer[1]');
 delete family.lexical_profile.availability_notes;
 vm.runInNewContext('renderLexical(family);',context);assert.equal(root.hidden,true);
});
