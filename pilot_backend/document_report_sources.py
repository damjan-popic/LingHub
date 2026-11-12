"""Report-only provenance from retained inputs; never infer linguistic use."""
import hashlib
import json
import re
from urllib.parse import quote


def retained_writer_ids(snapshot, source_text):
    generation=(snapshot or {}).get('stages',{}).get('generation')or{}
    native=(generation.get('writer')or{}).get('native_translation')
    if isinstance(native,dict):
        if (native.get('source_sha256')==hashlib.sha256(source_text.encode()).hexdigest()
                and native.get('source_evidence_ids')==[]
                and isinstance(native.get('translation_profile_sha256'),str)
                and re.fullmatch(r'[0-9a-f]{64}',native['translation_profile_sha256'])):
            return []
        return None
    requests=(generation.get('writer')or{}).get('requests')
    if not isinstance(requests,list):return None
    matched=[]
    for observed in requests:
        request=observed.get('request')if isinstance(observed,dict)else None
        if not isinstance(request,dict):continue
        for message in request.get('messages',[]):
            if not isinstance(message,dict)or message.get('role')!='user':continue
            try:value=json.loads(message['content'])
            except (KeyError,TypeError,ValueError):continue
            if (not isinstance(value,dict)or value.get('contract')!='klikai.document-transform.v1'
                    or value.get('source_text')!=source_text
                    or (value.get('task')or{}).get('source_sha256')!=hashlib.sha256(source_text.encode()).hexdigest()
                    or not isinstance(value.get('source_evidence'),list)):continue
            rows=value['source_evidence']
            if any(not isinstance(r,dict)or not isinstance(r.get('passage_id'),str)for r in rows):continue
            matched.append([r['passage_id']for r in rows])
    # Do not guess which of several requests produced the saved result.
    return matched[0]if len(matched)==1 else None


def report_section(section, bundle, snapshot, api_root):
    """Add references to an export copy, leaving stored results unchanged."""
    selected=retained_writer_ids(snapshot,section['text'])
    references=[dict(row)for row in section.get('sources',[])]
    known={r.get('evidence_id')for r in references}
    captured=(bundle.get('source_bundle')or{}).get('matches',[])
    for row in captured:
        eid=row.get('evidence_id')
        if selected is None or eid not in selected or eid in known:continue
        source=row.get('source')or{}
        references.append({'evidence_id':eid,
            **{k:row[k]for k in ('title','source_label','url')if k in row},
            **{k:source[k]for k in ('source_id','source_revision','locator')if k in source}})
        known.add(eid)
    family=section.get('family_id')
    family_url=api_root+'/families/'+quote(family,safe='')if family else None
    used={eid for edit in (section.get('document_result')or{}).get('edits',[])for eid in edit.get('evidence_ids',[])}
    for number,row in enumerate(references,1):
        row['local_number']=number
        eid=row.get('evidence_id')
        row['report_role']=('edit_support'if eid in used else'writer_input_context'if selected is not None and eid in selected
                            else'retrieval_context'if selected is not None else'selection_unknown')
        if family_url and eid:
            row['frozen_source_url']=family_url+'/sources/'+quote(eid,safe='')+'?tier=answer'
    return {**section,'sources':references,'saved_result_url':family_url,
            'writer_input_selection':'retained'if selected is not None else'unknown',
            'unresolved_writer_evidence_ids':[eid for eid in selected if eid not in known]if selected is not None else[]}
