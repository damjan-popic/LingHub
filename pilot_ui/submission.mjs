// One unresolved submission per tab/account. No passwords or session tokens.
export function submissionJournal(storage, prefix) {
  const key=prefix+':pending-submission';
  return {
    read(owner) {
      try {
        const value=JSON.parse(storage.getItem(key));
        return value?.owner===owner && typeof value.payload?.idempotency_key==='string' &&
          typeof value.payload.question==='string' ? value : null;
      } catch {return null;}
    },
    prepare(owner, payload, uuid) {
      const prior=this.read(owner);
      const plain=value=>JSON.stringify(Object.fromEntries(Object.entries(value).filter(([k])=>k!=='idempotency_key').sort(([a],[b])=>a.localeCompare(b))));
      if(prior && plain(prior.payload)===plain(payload))return prior.payload;
      const body={...payload,idempotency_key:uuid()};
      try {storage.setItem(key,JSON.stringify({owner,payload:body}));} catch {}
      return body;
    },
    clear(){try {storage.removeItem(key);} catch {}},
  };
}

export function pollDecision({failures, started, now}) {
  return failures>=5 || now-started>=60*60*1000 ? 'pause' : 'continue';
}
