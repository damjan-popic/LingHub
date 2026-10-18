import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.hidden=false;this.textContent='';}
 setAttribute(k,v){this.attributes[k]=v;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){this.children=[];}
 after(node){this.next=node;}
}
const source=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
const renderer=source.slice(source.indexOf('function renderLexical('),source.indexOf('function renderFamily('));
const el=source.split('\n').find(line=>line.startsWith('function el('));
for(const id of ['word','phrase','variants','translation'])test('complete captured '+id+' cards preserve sections and expandable details',{skip:!process.env.KLIKAI_QUALITY_PROFILES},()=>{
 const cards=JSON.parse(fs.readFileSync(process.env.KLIKAI_QUALITY_PROFILES+'/'+id+'.cards.private.json'));
 const root=new Node('section');const context={document:{createElement:t=>new Node(t)},state:{locale:'sl'},$:()=>root,clear:n=>n.replaceChildren(),family:{lexical_profile:cards}};
 vm.runInNewContext(el+'\n'+renderer+'\nrenderLexical(family);',context);
 assert.equal(root.hidden,false);const sections=root.children.filter(n=>n.tag==='details');assert.equal(sections.length,cards.sections.length);
 for(let i=0;i<sections.length;i++){
  const articles=sections[i].children.filter(n=>n.tag==='article');assert.equal(articles.length,cards.sections[i].items.length);
  for(let j=0;j<articles.length;j++){
   const row=cards.sections[i].items[j];const article=articles[j];assert.equal(article.children[0].textContent,row.title);
   if(row.details?.length)assert(article.children.some(n=>n.tag==='details'&&n.children[0].textContent==='Podrobnosti vira'));
   if(row.forms?.length)assert(article.children.some(n=>n.tag==='details'&&n.children.length===row.forms.length+1));
  }
 }
});
