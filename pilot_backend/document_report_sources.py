"""Report-only provenance from retained inputs; never infer linguistic use."""
import hashlib
import json
import re
from urllib.parse import quote
from .store import digest,canonical


def bundled_reference_path(value):
    """Portable local destinations created by the report exporter, not uploads."""
    return (isinstance(value,str)and value.startswith('sources/')
        and all(part not in {'','.','..'}for part in value.split('/'))
        and re.fullmatch(r'[A-Za-z0-9_./-]+',value)is not None)


def captured_qualifications(record,locale='sl'):
    """Concise source-owned qualifiers, with multilingual form ownership."""
    from .source_reader import content,TERM_FIELDS
    raw=record.get('capture')or{};it=locale=='it';rows=[]
    for key,label in [('source_status',('Stato della fonte','Status vira')),
            ('normative_status',('Stato normativo','Normativni status')),
            ('categories',('Ambito','Področje')),('subcategories',('Sottoambito','Podpodročje')),
            ('geographical_scope',('Area','Območje')),('jurisdiction',('Giurisdizione','Jurisdikcija'))]:
        value=content(raw.get(key))
        if value:rows.append(label[0 if it else 1]+': '+value)
    for entry in raw.get('term_entries',[]):
        if not isinstance(entry,dict):continue
        owner=' — '.join(str(entry[k])for k in ('lang','language','term')if entry.get(k))
        qualifiers=[]
        for key in ('status','normativeStatus','administrativeStatus','usage','geographicalUsage',
                'definitionSource','contextSource','motivationSource'):
            value=content(entry.get(key))
            if value:qualifiers.append(TERM_FIELDS[key][it]+': '+value)
        if qualifiers:rows.append(owner+' · '+' · '.join(qualifiers))
    return rows


def retained_writer_ids(snapshot, source_text):
    generation=(snapshot or {}).get('stages',{}).get('generation')or{}
    native=(generation.get('writer')or{}).get('native_translation')
    if isinstance(native,dict):
        trace=generation.get('writer')or{}
        packet=trace.get('guidance_packet')
        if isinstance(packet,dict):
            prepared=trace.get('prepared_input')or{};prompt=prepared.get('rendered_prompt');span=prepared.get('original_span')or{}
            ids=packet.get('admitted_evidence_ids')
            if (packet.get('version')!='klikai.document-guidance.v1'
                    or packet.get('packet_sha256')!=digest({k:v for k,v in packet.items()if k!='packet_sha256'})
                    or packet.get('source_sha256')!=hashlib.sha256(source_text.encode()).hexdigest()
                    or native.get('source_sha256')!=packet['source_sha256']
                    or native.get('translation_profile_sha256')!=packet.get('profile_sha256')
                    or not isinstance(ids,list)or len(ids)!=len(set(ids))or native.get('source_evidence_ids')!=ids):return None
            if not trace.get('requests'):return [] # Prepared guidance never dispatched.
            if (not isinstance(prompt,str)or prepared.get('guidance_packet_sha256')!=packet['packet_sha256']
                    or prepared.get('rendered_prompt_sha256')!=hashlib.sha256(prompt.encode()).hexdigest()
                    or type(span.get('start'))is not int or type(span.get('end'))is not int
                    or prompt[span['start']:span['end']]!=source_text
                    or len(trace['requests'])!=1 or trace['requests'][0].get('rendered_prompt')!=prompt):return None
            from .private_translation import native_references
            references=native_references(packet)
            block='BEGIN QUALIFIED KLIKAI REFERENCES\n'+canonical(references)+'\nEND QUALIFIED KLIKAI REFERENCES'
            if block not in prompt[:span['start']]:return None
            return ids
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
    review=(section.get('document_result')or{}).get('review')
    selected=[]if review is not None else retained_writer_ids(snapshot,section['text'])
    finding_ids={r['evidence_id']for finding in (review or{}).get('findings',[])for r in finding['related_source_captures']}
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
        row['report_role']=('edit_support'if eid in used else'finding_evidence'if eid in finding_ids else'review_context'if review is not None else'writer_input_context'if selected is not None and eid in selected
                            else'retrieval_context'if selected is not None else'selection_unknown')
        if family_url and eid:
            row['frozen_source_url']=family_url+'/sources/'+quote(eid,safe='')+'?tier=answer'
    return {**section,'sources':references,'saved_result_url':family_url,
            'writer_input_selection':'not_invoked'if review is not None else'retained'if selected is not None else'unknown',
            'unresolved_writer_evidence_ids':[eid for eid in selected if eid not in known]if selected is not None else[]}
