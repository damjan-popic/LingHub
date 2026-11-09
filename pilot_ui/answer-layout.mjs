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
