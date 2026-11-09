// One unresolved submission per tab/account. No passwords or session tokens.
export function submissionJournal(storage, prefix) {
  const key=prefix+':pending-submission';
  const drafts=new Map(),draftKey=owner=>prefix+':composer-drafts:'+encodeURIComponent(owner);
  function ownedDrafts(owner){
    if(!drafts.has(owner)){let saved={};try{const value=JSON.parse(storage.getItem(draftKey(owner)));if(value&&typeof value==='object'&&!Array.isArray(value))saved=value;}catch{}drafts.set(owner,saved);}
    return drafts.get(owner);
  }
  function persistDrafts(owner){try{storage.setItem(draftKey(owner),JSON.stringify(ownedDrafts(owner)));}catch{}}
  return {
    readDraft(owner,family=null){const value=owner&&ownedDrafts(owner)[family||'new'];return typeof value?.question==='string'?JSON.parse(JSON.stringify(value)):null;},
    saveDraft(owner,family,value){if(!owner||typeof value?.question!=='string')return;ownedDrafts(owner)[family||'new']=JSON.parse(JSON.stringify(value));persistDrafts(owner);},
    clearDraft(owner,family=null){if(!owner)return;delete ownedDrafts(owner)[family||'new'];persistDrafts(owner);},
    clearDrafts(owner){if(!owner)return;drafts.delete(owner);try{storage.removeItem(draftKey(owner));}catch{}},
    read(owner) {
      try {
        const value=JSON.parse(storage.getItem(key));
        return value?.owner===owner && typeof value.payload?.idempotency_key==='string' &&
          typeof value.payload.question==='string' ? value : null;
      } catch {return null;}
    },
    prepare(owner, payload, uuid, draftFamily=undefined) {
      const prior=this.read(owner);
      const plain=value=>JSON.stringify(Object.fromEntries(Object.entries(value).filter(([k])=>k!=='idempotency_key').sort(([a],[b])=>a.localeCompare(b))));
      if(prior && plain(prior.payload)===plain(payload))return prior.payload;
      const body={...payload,idempotency_key:uuid()};
      try {storage.setItem(key,JSON.stringify({owner,payload:body,...(draftFamily===undefined?{}:{draft_family:draftFamily})}));} catch {}
      return body;
    },
    clear(){try {storage.removeItem(key);} catch {}},
  };
}

export function pollDecision({failures, started, now}) {
  return failures>=5 || now-started>=60*60*1000 ? 'pause' : 'continue';
}
