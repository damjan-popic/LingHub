"""Server-owned document overview and separately stored optional summary."""
from copy import deepcopy
from .store import digest
from .security import Failure

MANIFEST='klikai.document-review-manifest.v1'
PACKET='klikai.document-summary-packet.v1'
KINDS=('qualified_advice','recorded_contrast','recorded_form_context','executable_application','application_proposal')


def manifest(document):
    if document.get('options',{}).get('review_strategy')!='evidence_review_v1':return None
    sections=[];counts={k:0 for k in KINDS if k!='application_proposal'or any('application_proposal'in ((s.get('document_result')or{}).get('review')or{}).get('overview',{}).get('counts',{})for s in document['sections'])};checks={};included=0
    for section in document['sections']:
        review=(section.get('document_result')or{}).get('review')
        sections.append({'section_index':section['index'],'location':section['location'],
            'state':section['state'],'manifest_sha256':(review or{}).get('manifest_sha256'),
            'source_sha256':(review or{}).get('source_sha256'),
            'failed_operations':[v.get('operation','document_transform')for v in section.get('limitations',[])if v['code']=='document_transform_failed']})
        if review:
            for kind in counts:counts[kind]+=review['overview']['counts'].get(kind,0)
            included+=review['overview']['included_edits']
            for check in review['checks']:
                group=checks.setdefault(check['check_id'],{});group[check['status']]=group.get(check['status'],0)+1
    value={'version':MANIFEST,'document_id':document['document_id'],'document_revision':document['source_sha256'],
        'strategy':'evidence_review_v1','processing':document['state'],'sections':sections,
        'counts':counts,'included_edits':included,'check_states':checks,
        'coverage':'bounded_recorded_source_review','human_acceptance':'not_recorded',
        'writer_invoked_for_review':False}
    value['manifest_sha256']=digest(value)
    return value


def packet(document,bundles):
    overview=manifest(document)
    if (overview is None or document['state']!='complete'or any(
            section['state']!='complete'or not section['manifest_sha256']for section in overview['sections'])):
        raise Failure('document_summary_unavailable',409)
    findings=[];sources={}
    for section,bundle in zip(document['sections'],bundles):
        review=section['document_result']['review']
        rows={r['evidence_id']:r for r in bundle['source_bundle']['matches']}
        for finding in review['findings']:
            source=finding['source'];row=rows.get(source['evidence_id'])
            if row is None or row['capture_sha256']!=source['capture_sha256']:
                raise Failure('document_summary_unavailable',409)
            raw=row['capture'];body=raw.get('original_text')
            if not isinstance(body,str):raise Failure('document_summary_unavailable',409)
            # Whole governing source text is retained, deduplicated, never clipped.
            native={key:deepcopy(raw[key])for key in ('title','source_revision','source_status','source_date','edition',
                'release_id','normative_status','coverage_note','rights_mode','url')if key in raw}
            native.update(source_slug=row['source_slug'],external_id=row['external_id'],original_text=body)
            key='S'+digest(native)[:24];sources[key]=native
            item={key:deepcopy(finding[key])for key in ('finding_id','type','applicability','match',
                'proposed_edit','included_in_draft','human_acceptance','notice','observed_lexical_candidates')if key in finding}
            item.update(section_index=section['index'],document_location=section['location'],
                source_ref=key,source_focus=deepcopy(source['focus']),
                recorded_advice=deepcopy(source.get('recorded_advice')),
                qualifications=deepcopy(source['qualifications']),
                untyped_context_limit=source.get('untyped_context_limit'))
            if finding.get('application_proof'):
                proof=finding['application_proof'];unit=proof.get('application_unit',{})
                item['application_identity']={'adapter_version':proof['adapter_version'],
                    'unit_type':unit.get('unit_type',proof.get('kind')),'unit_id':unit.get('unit_id'),'proof_sha256':proof['proof_sha256']}
            findings.append(item)
    result={'version':PACKET,'document_id':document['document_id'],'document_revision':document['source_sha256'],
        'manifest_sha256':overview['manifest_sha256'],'locale':document['options']['locale'],
        'server_overview':overview,'findings':findings,'sources':sources,
        'scope':'Completed bounded evidence review; unresolved applicability remains unresolved. Summary has no correction authority.'}
    result['packet_sha256']=digest(result)
    return result


def public_summary(saved,current_manifest):
    if not saved:return None
    value={key:deepcopy(saved[key])for key in ('version','status','manifest_sha256','document_revision','output',
        'summary_sha256','attempted','authority','failure_type')if key in saved}
    value['historical']=saved.get('manifest_sha256')!=(current_manifest or{}).get('manifest_sha256')
    return value
